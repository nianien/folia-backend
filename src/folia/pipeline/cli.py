from __future__ import annotations

import argparse
import sqlite3

from .config import database_path
from .db import fetch_rows
from .dedupe import assign_pending_articles
from .extractor import fetch_fulltext, html_to_text
from .facts import facts_pending
from .model_client import create_model_client
from .poller import poll
from .synthesizer import synthesize_pending
from .text import clean_text

MIN_FULLTEXT_CHARS = 600  # entry 自带正文短于此 → 抓 URL 补全文(trafilatura)


def main(argv: list[str] | None = None) -> int:
    """唯一入口: `start` 起控制面板 + 应用内自检循环, 其余都在面板里操作。"""
    parser = argparse.ArgumentParser(prog="folia-pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="启动控制面板 + 自检循环")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.command == "start":
        import uvicorn

        from .panel.app import create_app

        uvicorn.run(create_app(database_path()), host=args.host, port=args.port)
        return 0
    return 1


def run_once(conn: sqlite3.Connection, settings: dict) -> int:
    """自检一轮: 抓取 → 抽取 → 分类 → 聚类 → 事实 → 成稿。每步只处理未完成项(幂等)。"""
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


if __name__ == "__main__":
    raise SystemExit(main())
