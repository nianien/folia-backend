"""Export synthesized clusters into a portable publish artifact (frontpage.json).

Platform-agnostic: pure stdlib, no DB driver. The JSON is the contract the
cloud side loads into Neon Postgres (stories + full-text search field + sources).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .viewer import (
    CITE_RE,
    PLACEHOLDER,
    PLACEHOLDER_BRACKET,
    cat_meta,
    cluster_image,
    first_image,
)

SCHEMA_VERSION = 1
_HEADING_RE = re.compile(r"^#{1,6}\s+")


def export_frontpage(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT c.id, c.title, c.source_count, c.synthesized_text, c.updated_at,
               c.synthesis_model,
               a.category AS category, a.source_tier AS tier,
               a.published_at AS published_at
        FROM clusters c
        LEFT JOIN articles a ON a.id = c.representative_article_id
        WHERE c.synthesis_status = 'ok'
        ORDER BY a.published_at DESC, c.id DESC
        """
    ).fetchall()

    stories = []
    for row in rows:
        title = (row["title"] or "").strip()
        synth = row["synthesized_text"] or ""
        if PLACEHOLDER in title:
            continue
        body_md = clean_markdown(synth)
        plain = markdown_to_plain(body_md)
        category = row["category"] or "uncategorized"
        label, _ = cat_meta(category)
        sources = load_sources(conn, row["id"])
        stories.append(
            {
                "key": story_key(sources, title),
                "id": row["id"],
                "title": title,
                "category": category,
                "category_label": label,
                "tier": row["tier"] or "",
                "dek": first_paragraph(plain),
                "image_url": cluster_image(conn, row["id"]),
                "published_at": row["published_at"] or row["updated_at"] or "",
                "source_count": row["source_count"] or 1,
                "synthesis_md": body_md,
                "synthesis_model": row["synthesis_model"] or "",
                "search_text": f"{title}\n{plain}",
                "sources": sources,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(stories),
        "stories": stories,
    }


def write_frontpage(conn: sqlite3.Connection, out_path: Path) -> int:
    payload = export_frontpage(conn)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload["count"]


def story_key(sources: list[dict], title: str) -> str:
    """Stable cross-run key so likes survive pipeline re-runs (cluster ids reset)."""
    basis = (sources[0]["url"] if sources else "") or title
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def load_sources(conn: sqlite3.Connection, cluster_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT source_no, source_name, title, url FROM cluster_sources "
        "WHERE cluster_id=? ORDER BY source_no",
        (cluster_id,),
    ).fetchall()
    return [
        {
            "no": row["source_no"],
            "name": row["source_name"],
            "title": row["title"],
            "url": row["url"],
        }
        for row in rows
    ]


def clean_markdown(markdown: str) -> str:
    """Drop the leading '# title' line and the trailing Sources section; strip
    the fulltextrss failure placeholder. Sources travel as structured data."""
    out: list[str] = []
    for raw in markdown.splitlines():
        line = raw.rstrip().replace(PLACEHOLDER_BRACKET, "")
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading.lower().startswith("source") or heading.startswith("来源"):
                break
        out.append(line)
    return "\n".join(out).strip()


def markdown_to_plain(markdown: str) -> str:
    lines = []
    for raw in markdown.splitlines():
        line = _HEADING_RE.sub("", raw.strip())
        line = CITE_RE.sub("", line)
        line = line.replace(PLACEHOLDER_BRACKET, "").strip()
        if line and line != "---":
            lines.append(line)
    return "\n".join(lines)


def first_paragraph(plain: str, limit: int = 160) -> str:
    for line in plain.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 12:
            continue
        if len(line) > limit:
            line = line[:limit].rstrip() + "…"
        return line
    return ""
