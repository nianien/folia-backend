from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from .config import database_path, load_settings, load_sources
from .crawler import crawl, is_paywalled
from .db import connect, fetch_rows, init_db, insert_article, mark_source_result, sync_sources
from .dedupe import assign_pending_articles
from .extractor import extract_text
from .facts import facts_pending
from .fetcher import fetch_source, parse_feed
from .model_client import create_model_client
from .synthesizer import synthesize_pending
from .viewer import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="frontpage-pipeline")
    parser.add_argument("--settings", default="config/settings.toml")
    parser.add_argument("--sources", default="config/sources.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("run-once")
    sub.add_parser("crawl-pending")
    sub.add_parser("extract-pending")
    sub.add_parser("facts-pending")
    sub.add_parser("synthesize-pending")
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    inspect = sub.add_parser("inspect-cluster")
    inspect.add_argument("cluster_id", type=int)
    fixture = sub.add_parser("ingest-fixture")
    fixture.add_argument("source_id")
    fixture.add_argument("feed_path")
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    conn = open_database(settings)

    if args.command == "init-db":
        print("database initialized")
        return 0
    if args.command == "run-once":
        return run_once(conn, args.sources, settings)
    if args.command == "crawl-pending":
        print(f"crawled {crawl_pending(conn, settings)} articles")
        return 0
    if args.command == "extract-pending":
        print(f"extracted {extract_pending(conn, settings)} articles")
        return 0
    if args.command == "facts-pending":
        print(f"generated facts for {facts_pending(conn, create_model_client(settings))} articles")
        return 0
    if args.command == "synthesize-pending":
        print(f"synthesized {synthesize_pending(conn, create_model_client(settings))} clusters")
        return 0
    if args.command == "serve":
        conn.close()
        serve(database_path(settings), args.host, args.port)
        return 0
    if args.command == "inspect-cluster":
        inspect_cluster(conn, args.cluster_id)
        return 0
    if args.command == "ingest-fixture":
        return ingest_fixture(conn, args.sources, args.source_id, Path(args.feed_path), settings)
    return 1


def open_database(settings: dict) -> sqlite3.Connection:
    conn = connect(database_path(settings))
    init_db(conn)
    return conn


def run_once(conn: sqlite3.Connection, sources_path: str, settings: dict) -> int:
    sources = load_sources(sources_path)
    sync_sources(conn, sources)
    network = settings.get("network", {})
    timeout = int(network.get("timeout_seconds", 15))
    user_agent = network.get("user_agent", "FrontpagePipeline/0.1")
    inserted = 0
    for source in sources:
        if not source.enabled:
            continue
        try:
            articles = fetch_source(source, timeout, user_agent)
        except RuntimeError as exc:
            mark_source_result(conn, source.id, str(exc))
            print(str(exc), file=sys.stderr)
            continue
        for article in articles:
            if insert_article(conn, article):
                inserted += 1
        mark_source_result(conn, source.id)
    print(f"inserted {inserted} articles")
    print(f"crawled {crawl_pending(conn, settings)} articles")
    print(f"extracted {extract_pending(conn, settings)} articles")
    threshold = float(settings.get("dedupe", {}).get("same_event_threshold", 0.42))
    print(f"assigned {assign_pending_articles(conn, threshold)} articles to clusters")
    model_client = create_model_client(settings)
    print(f"generated facts for {facts_pending(conn, model_client)} articles")
    print(f"synthesized {synthesize_pending(conn, model_client)} clusters")
    return 0


def ingest_fixture(
    conn: sqlite3.Connection,
    sources_path: str,
    source_id: str,
    feed_path: Path,
    settings: dict,
) -> int:
    sources = load_sources(sources_path)
    sync_sources(conn, sources)
    source = next((item for item in sources if item.id == source_id), None)
    if source is None:
        print(f"unknown source: {source_id}", file=sys.stderr)
        return 2
    articles = parse_feed(feed_path.read_bytes(), source)
    inserted = sum(1 for article in articles if insert_article(conn, article))
    print(f"inserted {inserted} fixture articles")
    threshold = float(settings.get("dedupe", {}).get("same_event_threshold", 0.42))
    print(f"assigned {assign_pending_articles(conn, threshold)} articles to clusters")
    return 0


def crawl_pending(conn: sqlite3.Connection, settings: dict) -> int:
    network = settings.get("network", {})
    extraction = settings.get("extraction", {})
    timeout = int(network.get("timeout_seconds", 15))
    user_agent = network.get("user_agent", "FrontpagePipeline/0.1")
    paywalled_domains = list(extraction.get("paywalled_domains", []))
    rows = fetch_rows(
        conn,
        """
        SELECT id, url
        FROM articles
        WHERE html IS NULL AND fetch_status IN ('pending', 'failed')
        """,
    )
    changed = 0
    for row in rows:
        if is_paywalled(row["url"], paywalled_domains):
            conn.execute("UPDATE articles SET fetch_status='skipped', extract_status='paywalled' WHERE id=?", (row["id"],))
            changed += 1
            continue
        try:
            html = crawl(row["url"], timeout, user_agent)
        except Exception as exc:  # network failures must not stop the batch
            conn.execute("UPDATE articles SET fetch_status='failed' WHERE id=?", (row["id"],))
            print(f"crawl failed for {row['id']}: {exc}", file=sys.stderr)
            continue
        conn.execute("UPDATE articles SET html=?, fetch_status='ok' WHERE id=?", (html, row["id"]))
        changed += 1
    conn.commit()
    return changed


def extract_pending(conn: sqlite3.Connection, settings: dict) -> int:
    min_chars = int(settings.get("extraction", {}).get("min_text_chars", 400))
    rows = fetch_rows(
        conn,
        """
        SELECT id, html, summary
        FROM articles
        WHERE extracted_text IS NULL
          AND (
            html IS NOT NULL
            OR extract_status='paywalled'
            OR (fetch_status='failed' AND summary IS NOT NULL)
          )
        """,
    )
    changed = 0
    for row in rows:
        if row["html"]:
            text, status = extract_text(row["html"], row["summary"], min_chars=min_chars)
        else:
            text, status = row["summary"] or "", "fallback_summary"
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
