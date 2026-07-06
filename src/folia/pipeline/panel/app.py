"""控制面板 FastAPI 应用。

- 预览(/、/cluster、/article): 复用 viewer 渲染。
- 设置(/admin): 一页三区 —— 抓取(状态/启停/立即跑/间隔)· 数据同步(DATABASE_URL)· 数据源(feed 增删/导入)。
配置全部在 db; 循环由应用内 PipelineRunner 后台线程负责。
"""
from __future__ import annotations

import base64
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import ollama
from .. import viewer as viewer_mod
from ..config import PROVIDERS, is_pg_dsn, load_settings
from ..db import connect
from . import settings as cfg_store
from .runner import PipelineRunner

# 选 provider + 模型的功能(embedding 单列, 固定本地 Ollama)
CHAT_FUNCTIONS = ["categorize", "synthesis", "facts"]

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


def create_app(db_path: Path) -> FastAPI:
    runner = PipelineRunner(db_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        runner.start()
        yield
        runner.stop()

    app = FastAPI(title="Folia 控制面板", lifespan=lifespan)

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        # 设了 FOLIA_PANEL_PASSWORD 就对 /admin/* 强制 HTTP Basic; 预览页不拦
        password = os.environ.get("FOLIA_PANEL_PASSWORD")
        if password and request.url.path.startswith("/admin"):
            user = os.environ.get("FOLIA_PANEL_USER", "admin")
            header = request.headers.get("authorization", "")
            ok = False
            if header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(header[6:]).decode("utf-8")
                    got_user, _, got_pw = decoded.partition(":")
                    ok = secrets.compare_digest(got_user, user) and secrets.compare_digest(got_pw, password)
                except Exception:
                    ok = False
            if not ok:
                return Response(
                    "Unauthorized", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="folia"'},
                )
        return await call_next(request)

    def db():
        return connect(db_path)

    # ---------- 预览(复用 viewer) ----------
    def _preview(path: str) -> HTMLResponse:
        status, html = viewer_mod.route_request(db_path, path)
        return HTMLResponse(html, status_code=status)

    @app.get("/", response_class=HTMLResponse)
    def preview_index(request: Request):
        return _preview("/?" + request.url.query if request.url.query else "/")

    @app.get("/cluster/{cid}", response_class=HTMLResponse)
    def preview_cluster(cid: str):
        return _preview(f"/cluster/{cid}")

    @app.get("/article/{aid}", response_class=HTMLResponse)
    def preview_article(aid: str):
        return _preview(f"/article/{aid}")

    def _back(msg: str, tab: str = "fetch") -> RedirectResponse:
        return RedirectResponse(f"/admin?tab={tab}&msg=" + quote(msg), status_code=303)

    # ---------- 设置(一页: 抓取 / 数据同步 / 数据源) ----------
    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request, msg: str = "", tab: str = "fetch"):
        conn = db()
        try:
            s = load_settings(conn)
            feeds = cfg_store.list_feeds(conn)
            directories = cfg_store.list_directories(conn)
        finally:
            conn.close()
        available = ollama.list_models(s["embeddings"]["url"])  # 本机已装模型
        providers_cfg = s["providers"]
        provider_rows = [  # 凭证区: 密钥只暴露「是否已设」, 不回显原值
            {
                "name": name, "label": label, "local": name == "ollama",
                "endpoint": providers_cfg.get(name, {}).get("endpoint", ""),
                "has_key": bool(providers_cfg.get(name, {}).get("api_key")),
            }
            for name, label, _ep, _kenv in PROVIDERS
        ]
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "nav": "admin", "msg": msg, "tab": tab, "status": runner.status,
                "enabled": s["loop"]["enabled"], "interval": s["loop"]["interval"],
                "database_url": s["database"]["url"], "feeds": feeds,
                "directories": directories,
                "chat_functions": CHAT_FUNCTIONS, "models": s["models"],
                "embedding_model": s["models"]["embedding"],
                "provider_options": [(name, label) for name, label, _e, _k in PROVIDERS],
                "providers": provider_rows,
                "available_models": available,
            },
        )

    # 抓取: 启停循环 / 立即跑 / 间隔
    @app.post("/admin/loop/start")
    def loop_start():
        conn = db(); cfg_store.set_many(conn, {"loop.enabled": "1"}); conn.close()
        runner.notify()
        return _back("已启动循环 ✓")

    @app.post("/admin/loop/stop")
    def loop_stop():
        conn = db(); cfg_store.set_many(conn, {"loop.enabled": "0"}); conn.close()
        runner.notify()
        return _back("已停止循环")

    @app.post("/admin/loop/run")
    def loop_run():
        runner.trigger_now()
        return _back("已触发立即抓取")

    @app.post("/admin/interval")
    def set_interval(loop_interval: str = Form("1800")):
        conn = db(); cfg_store.set_many(conn, {"loop.interval": loop_interval}); conn.close()
        runner.notify()
        return _back("抓取间隔已保存 ✓")

    # 数据同步: DATABASE_URL(框里是什么就存什么; 空=只本地; 非空必须 postgres://)
    @app.post("/admin/database")
    def set_database(database_url: str = Form("")):
        dsn = database_url.strip()
        if dsn and not is_pg_dsn(dsn):
            return _back("数据库地址需以 postgres:// 开头，未更新", "sync")
        conn = db(); cfg_store.set_many(conn, {"database.url": dsn}); conn.close()
        runner.notify()
        return _back("数据同步已保存 ✓", "sync")

    # 数据源: 本地 feed 表就是真身, 无账号/无 API
    @app.post("/admin/sources/add")
    def sources_add(
        feed_url: str = Form(...),
        name: str = Form(""),
        description: str = Form(""),
    ):
        conn = db()
        cfg_store.add_feed(conn, feed_url.strip(), name.strip(), description.strip())
        conn.close()
        return _back("已添加数据源 ✓", "sources")

    @app.post("/admin/sources/remove")
    def sources_remove(url: str = Form(...)):
        conn = db()
        cfg_store.remove_feed(conn, url)
        conn.close()
        return _back("已删除数据源", "sources")

    @app.post("/admin/sources/import-seed")
    def sources_import():
        conn = db()
        added = cfg_store.import_default_feeds(conn)
        conn.close()
        return _back(f"导入默认订阅 +{added}", "sources")

    # 目录: 分类目录增删(驱动新闻分类与预览页 tab)
    @app.post("/admin/directory/add")
    def directory_add(
        name: str = Form(...),
        description: str = Form(""),
        color: str = Form("#7a6f5c"),
        sort_order: str = Form("50"),
    ):
        try:
            order = int(sort_order)
        except ValueError:
            order = 50
        conn = db()
        cfg_store.add_directory(conn, name.strip(), description.strip(), color.strip(), order)
        conn.close()
        return _back("目录已保存 ✓", "directory")

    @app.post("/admin/directory/remove")
    def directory_remove(name: str = Form(...)):
        conn = db()
        cfg_store.remove_directory(conn, name)
        conn.close()
        return _back("目录已删除", "directory")

    # 供应商: 各 provider 的 endpoint 与 API key(密钥留空=不改, 不会清空已存)
    @app.post("/admin/providers")
    async def set_providers(request: Request):
        form = await request.form()
        values: dict[str, str] = {}
        for name, _label, _ep, _kenv in PROVIDERS:
            endpoint = str(form.get(f"{name}_endpoint", "")).strip()
            if endpoint:
                values[f"providers.{name}.endpoint"] = endpoint
            key = str(form.get(f"{name}_key", "")).strip()
            if key:  # 留空表示保持原密钥不动
                values[f"providers.{name}.api_key"] = key
        if values:
            conn = db(); cfg_store.set_many(conn, values); conn.close()
            runner.notify()
        return _back("供应商配置已保存 ✓", "models")

    # 模型: embedding 固定本地 Ollama(填模型名); 其余功能各选 provider + 模型
    #       (provider 留空 = 规则/不用模型)
    @app.post("/admin/models")
    def set_models(
        embedding: str = Form(""),
        categorize_provider: str = Form(""),
        categorize_model: str = Form(""),
        synthesis_provider: str = Form(""),
        synthesis_model: str = Form(""),
        facts_provider: str = Form(""),
        facts_model: str = Form(""),
    ):
        values = {"models.embedding": embedding.strip()}
        for fn, provider, model in (
            ("categorize", categorize_provider, categorize_model),
            ("synthesis", synthesis_provider, synthesis_model),
            ("facts", facts_provider, facts_model),
        ):
            values[f"models.{fn}.provider"] = provider.strip()
            values[f"models.{fn}.model"] = model.strip()
        conn = db(); cfg_store.set_many(conn, values); conn.close()
        runner.notify()
        return _back("模型配置已保存 ✓", "models")

    return app
