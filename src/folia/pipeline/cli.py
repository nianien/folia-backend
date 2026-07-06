from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from .config import database_path, load_settings
from .db import connect, fetch_rows, init_db
from .dedupe import assign_pending_articles
from .extractor import fetch_fulltext, html_to_text
from .facts import facts_pending
from .model_client import create_model_client
from .poller import poll
from .store.export import write_frontpage
from .synthesizer import synthesize_pending
from .text import clean_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="folia-pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("run-once")
    sub.add_parser("extract-pending")
    sub.add_parser("categorize-pending")
    sub.add_parser("facts-pending")
    sub.add_parser("synthesize-pending")
    panel_parser = sub.add_parser("panel")
    panel_parser.add_argument("--host", default="127.0.0.1")
    panel_parser.add_argument("--port", type=int, default=8000)
    export_parser = sub.add_parser("export")
    export_parser.add_argument("--out", default="data/frontpage.json")
    load_parser = sub.add_parser("load")
    load_parser.add_argument("--in", dest="infile", default="data/frontpage.json")
    inspect = sub.add_parser("inspect-cluster")
    inspect.add_argument("cluster_id", type=int)
    fixture = sub.add_parser("ingest-fixture")
    fixture.add_argument("feed_path")
    args = parser.parse_args(argv)

    conn = open_database()               # db 路径是唯一引导项
    settings = load_settings(conn)        # 其余配置从 db 读

    if args.command == "init-db":
        print("database initialized")
        return 0
    if args.command == "run-once":
        return run_once(conn, settings)
    if args.command == "extract-pending":
        print(f"extracted {extract_pending(conn, settings)} articles")
        return 0
    if args.command == "categorize-pending":
        print(f"categorized {categorize_pending(conn, settings)} articles")
        return 0
    if args.command == "facts-pending":
        print(f"generated facts for {facts_pending(conn, create_model_client(settings, 'facts'))} articles")
        return 0
    if args.command == "synthesize-pending":
        print(f"synthesized {synthesize_pending(conn, create_model_client(settings, 'synthesis'))} clusters")
        return 0
    if args.command == "export":
        count = write_frontpage(conn, Path(args.out))
        print(f"exported {count} stories to {args.out}")
        return 0
    if args.command == "load":
        dsn = settings.get("database", {}).get("url")
        if not dsn:
            print("database.url 未配置(面板 → 配置)", file=sys.stderr)
            return 2
        from .store.loader import load as load_stories  # lazy: only this cmd needs psycopg

        total, active = load_stories(Path(args.infile), dsn)
        print(f"loaded {total} stories ({active} active)")
        return 0
    if args.command == "panel":
        conn.close()  # 面板/循环各自开连接
        import uvicorn

        from .panel.app import create_app

        app = create_app(database_path())
        uvicorn.run(app, host=args.host, port=args.port)
        return 0
    if args.command == "inspect-cluster":
        inspect_cluster(conn, args.cluster_id)
        return 0
    if args.command == "ingest-fixture":
        return ingest_fixture(conn, Path(args.feed_path), settings)
    return 1


def open_database() -> sqlite3.Connection:
    conn = connect(database_path())
    init_db(conn)
    return conn


def run_once(conn: sqlite3.Connection, settings: dict) -> int:
    print(f"inserted {poll(conn, settings)} articles")
    print(f"extracted {extract_pending(conn, settings)} articles")
    print(f"categorized {categorize_pending(conn, settings)} articles")
    print(f"assigned {assign_pending_articles(conn, settings)} articles to clusters")
    print(f"generated facts for {facts_pending(conn, create_model_client(settings, 'facts'))} articles")
    print(f"synthesized {synthesize_pending(conn, create_model_client(settings, 'synthesis'))} clusters")
    return 0


def categorize_pending(conn: sqlite3.Connection, settings: dict, limit: int = 40) -> int:
    """给还没分类的文章按内容定目录(LLM); 每轮限量, 积压分多轮消化。"""
    from .categorize import classify

    rows = conn.execute("SELECT name, parent FROM directory ORDER BY sort_order, name").fetchall()
    tops = [r["name"] for r in rows if not r["parent"]]
    if not tops:
        return 0
    tree = [(top, [r["name"] for r in rows if r["parent"] == top]) for top in tops]
    client = create_model_client(settings, "categorize")
    if not client.enabled:
        return 0
    rows = fetch_rows(
        conn,
        "SELECT id, title, extracted_text, summary FROM articles "
        "WHERE category IS NULL OR category='' LIMIT ?",
        (limit,),
    )
    changed = 0
    for row in rows:
        text = row["extracted_text"] or row["summary"] or ""
        category = classify(row["title"], text, tree, client)
        conn.execute("UPDATE articles SET category=? WHERE id=?", (category, row["id"]))
        changed += 1
    conn.commit()
    return changed


def ingest_fixture(conn: sqlite3.Connection, feed_path: Path, settings: dict) -> int:
    """离线摄取: 把本地 feed 文件当成一个源, 走轮询器解析入库(无网络)。"""
    conn.execute(
        "INSERT OR IGNORE INTO feed (url, name, description) VALUES (?,?,?)",
        (str(feed_path), "fixture", "本地样本"),
    )
    conn.commit()
    print(f"inserted {poll(conn, settings)} fixture articles")
    print(f"extracted {extract_pending(conn, settings)} articles")
    print(f"assigned {assign_pending_articles(conn, settings)} articles to clusters")
    return 0


MIN_FULLTEXT_CHARS = 600  # entry 自带正文短于此 → 抓 URL 补全文(trafilatura)


def extract_pending(conn: sqlite3.Connection, settings: dict) -> int:
    rows = fetch_rows(
        conn,
        """
        SELECT id, url, content_html, summary
        FROM articles
        WHERE extracted_text IS NULL
        """,
    )
    changed = 0
    for row in rows:
        text = html_to_text(row["content_html"])
        status = "ok"
        if len(text) < MIN_FULLTEXT_CHARS and row["url"]:
            try:
                fetched = fetch_fulltext(row["url"])
            except Exception:
                fetched = ""
            if len(fetched) > len(text):
                text, status = fetched, "ok_fulltext"
        if not text:
            if clean_text(row["summary"]):
                text, status = clean_text(row["summary"]), "fallback_summary"
            else:
                text, status = "", "empty"
        conn.execute(
            "UPDATE articles SET extracted_text=?, extract_status=? WHERE id=?",
            (text, status, row["id"]),
        )
        changed += 1
    conn.commit()
    return changed


def inspect_cluster(conn: sqlite3.Connection, cluster_id: int) -> None:
    cluster = conn.execute("SELECT * FROM clusters WHERE id=?", (cluster_id,)).fetchone()
    if cluster is None:
        print(f"cluster {cluster_id} not found", file=sys.stderr)
        return
    print(f"# cluster {cluster_id}: {cluster['title']}")
    print()
    print(cluster["synthesized_text"] or "(not synthesized)")
    print()
    for row in conn.execute("SELECT * FROM cluster_sources WHERE cluster_id=? ORDER BY source_no", (cluster_id,)):
        print(f"[{row['source_no']}] {row['source_name']} · {row['title']} · {row['url']}")


if __name__ == "__main__":
    raise SystemExit(main())
