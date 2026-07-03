"""控制面板 FastAPI 应用。

- 预览(/、/cluster、/article): 复用 viewer 渲染。
- 控制台(/admin): 状态 + 启停循环 + 立即跑。
- 配置(/admin/config): 编辑 db 配置(点分键: freshrss.* / embeddings.* / dedupe.* / model.provider /
  database.url / loop.interval), 可测 FreshRSS 连接。
- 数据源(/admin/sources): 管 FreshRSS 订阅 + tier/category 映射(source_map)。
配置全部在 db; 循环由应用内 PipelineRunner 后台线程负责。
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import viewer as viewer_mod
from ..config import load_settings
from ..db import connect
from ..freshrss_client import FreshRSSClient, FreshRSSError
from . import store
from .runner import PipelineRunner

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
OPML_PATH = Path("config/freshrss/subscriptions.opml")


def create_app(db_path: Path) -> FastAPI:
    runner = PipelineRunner(db_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        runner.start()
        yield
        runner.stop()

    app = FastAPI(title="Folia 控制面板", lifespan=lifespan)

    def db():
        return connect(db_path)

    def client(conn) -> FreshRSSClient:
        return FreshRSSClient.from_settings(load_settings(conn))

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
        conn = db(); store.set_many(conn, {"loop.enabled": "1"}); conn.close()
        runner.notify()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/loop/stop")
    def loop_stop():
        conn = db(); store.set_many(conn, {"loop.enabled": "0"}); conn.close()
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
        freshrss_api_url: str = Form(""),
        freshrss_user: str = Form(""),
        freshrss_api_password: str = Form(""),
        freshrss_batch_size: str = Form("100"),
        freshrss_mark_read: str = Form(""),
        embeddings_url: str = Form(""),
        embeddings_model: str = Form("bge-m3"),
        dedupe_same_event_threshold: str = Form("0.85"),
        dedupe_jaccard_threshold: str = Form("0.42"),
        model_provider: str = Form("heuristic"),
        database_url: str = Form(""),
        loop_interval: str = Form("1800"),
    ):
        conn = db()
        store.set_many(
            conn,
            {
                "freshrss.api_url": freshrss_api_url,
                "freshrss.user": freshrss_user,
                "freshrss.api_password": freshrss_api_password,
                "freshrss.batch_size": freshrss_batch_size,
                "freshrss.mark_read": "1" if freshrss_mark_read else "0",
                "embeddings.url": embeddings_url,
                "embeddings.model": embeddings_model,
                "dedupe.same_event_threshold": dedupe_same_event_threshold,
                "dedupe.jaccard_threshold": dedupe_jaccard_threshold,
                "model.provider": model_provider,
                "database.url": database_url,
                "loop.interval": loop_interval,
            },
        )
        conn.close()
        runner.notify()
        return RedirectResponse("/admin/config?msg=" + quote("已保存 ✓"), status_code=303)

    @app.post("/admin/config/test")
    def config_test():
        conn = db()
        try:
            client(conn).login()
            msg = "FreshRSS 连接成功 ✓"
        except Exception as exc:
            msg = f"连接失败: {exc}"
        finally:
            conn.close()
        return RedirectResponse("/admin/config?msg=" + quote(msg), status_code=303)

    # ---------- 数据源 ----------
    @app.get("/admin/sources", response_class=HTMLResponse)
    def sources_get(request: Request, msg: str = ""):
        conn = db()
        subs: list[dict] = []
        error = ""
        try:
            subs = client(conn).list_subscriptions()
        except Exception as exc:
            error = str(exc)
        mappings = store.list_source_map(conn)
        conn.close()
        return templates.TemplateResponse(
            request,
            "sources.html",
            {"nav": "sources", "subs": subs, "mappings": mappings, "error": error, "msg": msg},
        )

    @app.post("/admin/sources/add")
    def sources_add(feed_url: str = Form(...)):
        conn = db()
        try:
            client(conn).add_subscription(feed_url.strip())
            msg = "已添加 ✓"
        except Exception as exc:
            msg = f"失败: {exc}"
        finally:
            conn.close()
        return RedirectResponse("/admin/sources?msg=" + quote(msg), status_code=303)

    @app.post("/admin/sources/remove")
    def sources_remove(stream_id: str = Form(...)):
        conn = db()
        try:
            client(conn).remove_subscription(stream_id)
            msg = "已删除 ✓"
        except Exception as exc:
            msg = f"失败: {exc}"
        finally:
            conn.close()
        return RedirectResponse("/admin/sources?msg=" + quote(msg), status_code=303)

    @app.post("/admin/sources/import-opml")
    def sources_import():
        conn = db()
        added = 0
        try:
            cl = client(conn)
            for url in _opml_feed_urls(OPML_PATH):
                try:
                    cl.add_subscription(url)
                    added += 1
                except Exception:
                    pass
            msg = f"从 OPML 导入 {added} 个订阅"
        except Exception as exc:
            msg = f"失败: {exc}"
        finally:
            conn.close()
        return RedirectResponse("/admin/sources?msg=" + quote(msg), status_code=303)

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
        store.set_source_map(conn, match_type, match_key.strip(), name.strip(), tier.strip(), category.strip())
        conn.close()
        return RedirectResponse("/admin/sources?msg=" + quote("映射已保存 ✓"), status_code=303)

    @app.post("/admin/sources/map/remove")
    def map_remove(match_type: str = Form(...), match_key: str = Form(...)):
        conn = db()
        store.delete_source_map(conn, match_type, match_key)
        conn.close()
        return RedirectResponse("/admin/sources?msg=" + quote("映射已删除 ✓"), status_code=303)

    return app


def _opml_feed_urls(path: Path) -> list[str]:
    if not path.exists():
        return []
    import html as _html

    text = path.read_text(encoding="utf-8")
    return [_html.unescape(u) for u in re.findall(r'xmlUrl="([^"]+)"', text)]
