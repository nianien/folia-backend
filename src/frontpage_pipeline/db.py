from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import FeedArticle
from .text import content_hash, normalize_url, stable_id


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  tier TEXT NOT NULL,
  category_hint TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_fetched_at TEXT,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS articles (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_tier TEXT,
  category TEXT,
  external_id TEXT,
  guid TEXT,
  url TEXT NOT NULL,
  canonical_url TEXT,
  title TEXT NOT NULL,
  summary TEXT,
  content_html TEXT,
  published_at TEXT,
  fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  extracted_text TEXT,
  article_facts TEXT,
  extract_status TEXT,
  fact_status TEXT,
  content_hash TEXT,
  cluster_id INTEGER,
  UNIQUE(source_id, guid),
  UNIQUE(canonical_url),
  UNIQUE(external_id)
);

CREATE TABLE IF NOT EXISTS clusters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  representative_article_id TEXT,
  title TEXT,
  centroid BLOB,
  source_count INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  synthesized_text TEXT,
  synthesis_status TEXT,
  synthesis_model TEXT,
  synthesis_updated_at TEXT,
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS cluster_articles (
  cluster_id INTEGER NOT NULL,
  article_id TEXT NOT NULL,
  similarity REAL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (cluster_id, article_id)
);

CREATE TABLE IF NOT EXISTS cluster_sources (
  cluster_id INTEGER NOT NULL,
  source_no INTEGER NOT NULL,
  article_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  published_at TEXT,
  PRIMARY KEY (cluster_id, source_no),
  UNIQUE(cluster_id, article_id)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_source(
    conn: sqlite3.Connection,
    source_id: str,
    name: str,
    tier: str,
    category: str | None = None,
) -> None:
    """Register a FreshRSS feed observed during ingest, so the viewer can list it."""
    conn.execute(
        """
        INSERT INTO sources (id, name, url, tier, category_hint, enabled, last_fetched_at)
        VALUES (?, ?, '', ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          tier=excluded.tier,
          category_hint=excluded.category_hint,
          last_fetched_at=CURRENT_TIMESTAMP
        """,
        (source_id, name, tier, category),
    )
    conn.commit()


def insert_article(conn: sqlite3.Connection, article: FeedArticle) -> str | None:
    canonical_url = normalize_url(article.url)
    article_id = stable_id(article.source_id, article.guid or canonical_url, article.title)
    digest = content_hash(article.title, article.summary)
    try:
        conn.execute(
            """
            INSERT INTO articles (
              id, source_id, source_name, source_tier, category, external_id, guid,
              url, canonical_url, title, summary, content_html, published_at, content_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id,
                article.source_id,
                article.source_name,
                article.source_tier,
                article.category,
                article.external_id,
                article.guid,
                article.url,
                canonical_url,
                article.title,
                article.summary,
                article.content_html,
                article.published_at,
                digest,
            ),
        )
        conn.commit()
        return article_id
    except sqlite3.IntegrityError:
        return None


def fetch_rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    return list(conn.execute(query, params))
