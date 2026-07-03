"""Load a frontpage.json snapshot into Neon Postgres.

Snapshot semantics: every load marks the whole table inactive, then upserts the
current set as active. Content columns are overwritten; like_count is preserved
across re-runs (keyed by the stable story key).

Usage:
    DATABASE_URL=postgres://... python loader.py ../data/frontpage.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

UPSERT = """
INSERT INTO stories (
    story_id, title, category, category_label, tier, dek, image_url,
    published_at, source_count, synthesis_md, synthesis_model, search_text,
    sources, active, updated_at
) VALUES (
    %(id)s, %(title)s, %(category)s, %(category_label)s, %(tier)s,
    %(dek)s, %(image_url)s, %(published_at)s, %(source_count)s, %(synthesis_md)s,
    %(synthesis_model)s, %(search_text)s, %(sources)s, true, now()
)
ON CONFLICT (story_id) DO UPDATE SET
    title           = EXCLUDED.title,
    category        = EXCLUDED.category,
    category_label  = EXCLUDED.category_label,
    tier            = EXCLUDED.tier,
    dek             = EXCLUDED.dek,
    image_url       = EXCLUDED.image_url,
    published_at    = EXCLUDED.published_at,
    source_count    = EXCLUDED.source_count,
    synthesis_md    = EXCLUDED.synthesis_md,
    synthesis_model = EXCLUDED.synthesis_model,
    search_text     = EXCLUDED.search_text,
    sources         = EXCLUDED.sources,
    active          = true,
    updated_at      = now()
"""


def load(path: Path, dsn: str) -> tuple[int, int]:
    from ..config import is_pg_dsn

    if not is_pg_dsn(dsn):
        raise ValueError("DATABASE_URL 必须是 postgres:// 连接串")
    payload = json.loads(path.read_text(encoding="utf-8"))
    stories = payload.get("stories", [])
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
            cur.execute("UPDATE stories SET active = false")
            for story in stories:
                cur.execute(UPSERT, to_params(story))
            cur.execute("SELECT count(*) FROM stories WHERE active")
            active = cur.fetchone()[0]
        conn.commit()
    return len(stories), active


def to_params(story: dict) -> dict:
    params = {
        "id": story["id"],
        "title": story["title"],
        "category": story.get("category") or "uncategorized",
        "category_label": story.get("category_label"),
        "tier": story.get("tier"),
        "dek": story.get("dek"),
        "image_url": story.get("image_url"),
        "published_at": story.get("published_at") or None,
        "source_count": story.get("source_count") or 1,
        "synthesis_md": story.get("synthesis_md"),
        "synthesis_model": story.get("synthesis_model"),
        "search_text": story.get("search_text") or "",
        "sources": Jsonb(story.get("sources") or []),
    }
    return params


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("data/frontpage.json")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if not path.exists():
        print(f"{path} not found — run `folia-pipeline export` first", file=sys.stderr)
        return 2
    total, active = load(path, dsn)
    print(f"loaded {total} stories ({active} active) into Neon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
