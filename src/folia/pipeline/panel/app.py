"""控制面板 FastAPI 应用。

- 预览(/、/cluster、/article): 复用 viewer 渲染。
- 控制台(/admin): 状态 + 启停循环 + 立即跑。
- 配置(/admin/config): 编辑 db 配置(embeddings.* / dedupe.* / model.provider / database.url /
  loop.interval)。FreshRSS 为内嵌固定账号, 不在此配置。
- 数据源(/admin/sources): 管订阅源(本地 feed 表) + tier/category 映射(source_map)。
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

from .. import viewer as viewer_mod
from ..config import is_pg_dsn, load_settings
from ..db import connect
from . import settings as cfg_store
from .runner import PipelineRunner

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

    # ---------- 控制台 ----------
    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request):
        conn = db()
        try:
            s = load_settings(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "nav": "admin",
                "status": runner.status,
                "enabled": s["loop"]["enabled"],
                "interval": s["loop"]["interval"],
            },
        )

    @app.post("/admin/loop/start")
    def loop_start():
        conn = db(); cfg_store.set_many(conn, {"loop.enabled": "1"}); conn.close()
        runner.notify()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/loop/stop")
    def loop_stop():
        conn = db(); cfg_store.set_many(conn, {"loop.enabled": "0"}); conn.close()
        runner.notify()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/loop/run")
    def loop_run():
        runner.trigger_now()
        return RedirectResponse("/admin", status_code=303)

    # ---------- 配置 ----------
    @app.get("/admin/config", response_class=HTMLResponse)
    def config_get(request: Request, msg: str = ""):
        conn = db()
        try:
            s = load_settings(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "config.html", {"nav": "config", "s": s, "msg": msg}
        )

    @app.post("/admin/config")
    def config_post(
        embeddings_url: str = Form(""),
        embeddings_model: str = Form("bge-m3"),
        dedupe_same_event_threshold: str = Form("0.85"),
        dedupe_jaccard_threshold: str = Form("0.42"),
        model_provider: str = Form("heuristic"),
        database_url: str = Form(""),
        loop_interval: str = Form("1800"),
    ):
        values = {
            "embeddings.url": embeddings_url,
            "embeddings.model": embeddings_model,
            "dedupe.same_event_threshold": dedupe_same_event_threshold,
            "dedupe.jaccard_threshold": dedupe_jaccard_threshold,
            "model.provider": model_provider,
            "loop.interval": loop_interval,
        }
        # DATABASE_URL: 写入型——留空保持不变, 填 'none' 显式清空
        note = "已保存 ✓"
        if database_url:
            dsn = database_url.strip()
            if dsn.lower() == "none":
                values["database.url"] = ""
            elif is_pg_dsn(dsn):
                values["database.url"] = dsn
            else:
                note = "已保存(DATABASE_URL 必须 postgres:// 开头, 该项已忽略)"
        conn = db()
        cfg_store.set_many(conn, values)
        conn.close()
        runner.notify()
        return RedirectResponse("/admin/config?msg=" + quote(note), status_code=303)

    # ---------- 数据源(本地 feed 表就是真身, 无账号/无 API) ----------
    @app.get("/admin/sources", response_class=HTMLResponse)
    def sources_get(request: Request, msg: str = ""):
        conn = db()
        feeds = cfg_store.list_feeds(conn)
        mappings = cfg_store.list_source_map(conn)
        conn.close()
        return templates.TemplateResponse(
            request,
            "sources.html",
            {"nav": "sources", "feeds": feeds, "mappings": mappings, "msg": msg},
        )

    @app.post("/admin/sources/add")
    def sources_add(
        feed_url: str = Form(...),
        title: str = Form(""),
        tier: str = Form(""),
        category: str = Form(""),
    ):
        conn = db()
        cfg_store.add_feed(conn, feed_url.strip(), title.strip(), tier.strip(), category.strip())
        conn.close()
        return RedirectResponse("/admin/sources?msg=" + quote("已添加 ✓"), status_code=303)

    @app.post("/admin/sources/remove")
    def sources_remove(url: str = Form(...)):
        conn = db()
        cfg_store.remove_feed(conn, url)
        conn.close()
        return RedirectResponse("/admin/sources?msg=" + quote("已删除 ✓"), status_code=303)

    @app.post("/admin/sources/import-seed")
    def sources_import():
        conn = db()
        added = cfg_store.import_default_feeds(conn)
        conn.close()
        return RedirectResponse(
            "/admin/sources?msg=" + quote(f"导入默认订阅 +{added}"), status_code=303
        )

    # tier/category 映射
    @app.post("/admin/sources/map/add")
    def map_add(
        match_type: str = Form("title"),
        match_key: str = Form(...),
        name: str = Form(""),
        tier: str = Form("unknown"),
        category: str = Form("uncategorized"),
    ):
        conn = db()
        cfg_store.set_source_map(conn, match_type, match_key.strip(), name.strip(), tier.strip(), category.strip())
        conn.close()
        return RedirectResponse("/admin/sources?msg=" + quote("映射已保存 ✓"), status_code=303)

    @app.post("/admin/sources/map/remove")
    def map_remove(match_type: str = Form(...), match_key: str = Form(...)):
        conn = db()
        cfg_store.delete_source_map(conn, match_type, match_key)
        conn.close()
        return RedirectResponse("/admin/sources?msg=" + quote("映射已删除 ✓"), status_code=303)

    return app
