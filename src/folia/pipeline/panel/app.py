"""控制面板 FastAPI 应用。

- 预览(/、/cluster、/article): 复用 viewer 渲染。
- 设置(/admin): 一页三区 —— 抓取(状态/启停/立即跑/间隔)· 数据同步(DATABASE_URL)· 数据源(feed 增删/导入)。
配置全部在 db; 循环由应用内 PipelineRunner 后台线程负责。
"""
from __future__ import annotations

import base64
import os
import secrets
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import viewer as viewer_mod
from ..config import ROOT, EMBED_MODELS, PROVIDER_MODELS, PROVIDERS, is_pg_dsn, load_settings
from ..db import connect
from . import settings as cfg_store
from .runner import PipelineRunner

# 选 provider + 模型的功能(embedding 单列, 固定本地 Ollama)
CHAT_FUNCTIONS = ["analyze", "synthesis"]

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
            directories = cfg_store.list_directory_tree(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "nav": "admin", "msg": msg, "tab": tab, "status": runner.status,
                "interval": s.get("loop", {}).get("interval", 1800),
                "database_url": s.get("database", {}).get("url", ""), "feeds": feeds,
                "directories": directories,
                "chat_functions": CHAT_FUNCTIONS, "models": s.get("models", {}),
                "embedding_model": s.get("models", {}).get("embedding", ""),
                "provider_options": [(name, label) for name, label, _e, _k in PROVIDERS],
                "provider_models": PROVIDER_MODELS,
                "embed_models": EMBED_MODELS,
            },
        )

    # 抓取: 循环常开自检; 这里只有「立即跑一轮」和改间隔
    @app.post("/admin/loop/run")
    def loop_run():
        runner.trigger_now()
        return _back("已触发立即跑一轮")

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

    # 一键初始化: 以子进程跑 scripts/init_db.py(不 import 它), 幂等补齐默认 feeds/分类/配置
    @app.post("/admin/install")
    def run_install():
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "init_db.py")],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        runner.notify()
        if proc.returncode == 0:
            summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "完成"
            return _back(f"初始化完成 ✓ {summary}", "sources")
        return _back(f"初始化失败: {(proc.stderr or proc.stdout)[:160]}", "sources")

    # 新闻分类: 两级增删(一级 parent=''; 二级 parent=所属一级)。加一级自动带默认二级 "综合"
    @app.post("/admin/directory/add")
    def directory_add(
        name: str = Form(...),
        parent: str = Form(""),
        description: str = Form(""),
        color: str = Form("#7a6f5c"),
        sort_order: str = Form("50"),
    ):
        try:
            order = int(sort_order)
        except ValueError:
            order = 50
        conn = db()
        cfg_store.add_directory(
            conn, name.strip(), parent.strip(), description.strip(), color.strip(), order
        )
        conn.close()
        return _back("新闻分类已保存 ✓", "directory")

    @app.post("/admin/directory/remove")
    def directory_remove(name: str = Form(...), parent: str = Form("")):
        conn = db()
        cfg_store.remove_directory(conn, name.strip(), parent.strip())
        conn.close()
        return _back("已删除分类", "directory")

    # 模型: embedding 固定本地 Ollama; 其余功能各选 provider + 模型
    #       (provider 留空 = 规则/不用模型)。供应商 key/endpoint 从环境变量读, 不在页面配。
    @app.post("/admin/models")
    def set_models(
        embedding: str = Form(""),
        analyze_provider: str = Form(""),
        analyze_model: str = Form(""),
        synthesis_provider: str = Form(""),
        synthesis_model: str = Form(""),
    ):
        values = {"models.embedding": embedding.strip()}
        for fn, provider, model in (
            ("analyze", analyze_provider, analyze_model),
            ("synthesis", synthesis_provider, synthesis_model),
        ):
            values[f"models.{fn}.provider"] = provider.strip()
            values[f"models.{fn}.model"] = model.strip()
        conn = db(); cfg_store.set_many(conn, values); conn.close()
        runner.notify()
        return _back("模型配置已保存 ✓", "models")

    return app
