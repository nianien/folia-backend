from __future__ import annotations

import argparse
import sqlite3

from .analyze import analyze_pending
from .config import database_path
from .db import fetch_rows
from .dedupe import assign_pending_articles
from .extractor import fetch_fulltext, html_to_text
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


def run_once(conn: sqlite3.Connection, settings: dict, on_stage=None) -> int:
    """一轮: 爬取 → 解析 → 分类 → 聚合 → 提炼 → 合成。每步只处理未完成项(幂等, 限量)。

    on_stage(名称) 在进入每个阶段前回调, 供面板显示当前阶段(爬取中/解析中/...)。
    返回本轮"实际处理的项数"。>0 说明还有积压, 循环应尽快再跑一轮; =0 说明已消化完, 可歇到下个间隔。
    """
    def stage(name: str) -> None:
        if on_stage is not None:
            on_stage(name)

    stage("爬取中")
    n_poll = poll(conn, settings)
    stage("解析中")
    n_extract = extract_pending(conn, settings)
    stage("分析中")  # 分类 + 标签 + 提炼, 一次 LLM
    n_analyze = analyze_pending(conn, create_model_client(settings, "analyze"))
    stage("聚合中")  # 分析后聚: 用 summary 算 embedding, 同最细分类 + 24h 内
    assign_pending_articles(conn, settings)
    stage("合成中")
    n_synth = synthesize_pending(conn, create_model_client(settings, "synthesis"))
    print(f"poll={n_poll} extract={n_extract} analyze={n_analyze} synth={n_synth}")
    return n_poll + n_extract + n_analyze + n_synth


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
