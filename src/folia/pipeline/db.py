from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import FeedArticle
from .text import content_hash, normalize_url, stable_id


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS feed (
  url TEXT PRIMARY KEY,        -- 订阅源(本地即真身): 自写轮询器直接抓这些
  name TEXT,                   -- 源名称(如 BBC World)
  description TEXT,            -- 一句话介绍
  etag TEXT,                   -- 上轮响应 ETag, 下轮条件请求(省带宽/挡未变)
  modified TEXT,               -- 上轮 Last-Modified
  last_fetched_at TEXT,
  last_status TEXT,            -- 'ok: +N' | 'error: ...'
  enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS directory (
  name TEXT NOT NULL,          -- 分类名; 一级即 category 一段, 二级即 "一级/二级" 后半段
  parent TEXT NOT NULL DEFAULT '',  -- '' = 一级; 否则 = 所属一级名
  description TEXT,            -- 给分类器/人看的说明
  color TEXT,                  -- 预览页强调色
  sort_order INTEGER NOT NULL DEFAULT 50,
  PRIMARY KEY (parent, name)   -- "综合" 可挂多个一级下
);

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
  synthesis_zh TEXT,
  synthesis_en TEXT,
  synthesis_status TEXT,
  synthesis_model TEXT,
  synthesis_updated_at TEXT,
  status TEXT NOT NULL DEFAULT 'active'
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
    conn.execute("PRAGMA busy_timeout=5000")  # 撞锁等 5s, 挡循环写 vs Web 写冲突
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表 + 迁移(幂等,数据无关)。初始数据由 scripts/init_db.py 一次性写入,不在这里播种。"""
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """给既有库补新列/结构(CREATE TABLE IF NOT EXISTS 不动已存在的表)。幂等。"""
    cluster_cols = {r[1] for r in conn.execute("PRAGMA table_info(clusters)")}
    for col in ("synthesis_zh", "synthesis_en"):
        if col not in cluster_cols:
            conn.execute(f"ALTER TABLE clusters ADD COLUMN {col} TEXT")

    # directory 旧结构是扁平(主键 name, 无 parent) → 重建为两级, 旧行迁成一级
    dir_cols = {r[1] for r in conn.execute("PRAGMA table_info(directory)")}
    if "parent" not in dir_cols:
        conn.execute("ALTER TABLE directory RENAME TO directory_old")
        conn.executescript(
            """
            CREATE TABLE directory (
              name TEXT NOT NULL, parent TEXT NOT NULL DEFAULT '',
              description TEXT, color TEXT, sort_order INTEGER NOT NULL DEFAULT 50,
              PRIMARY KEY (parent, name)
            );
            """
        )
        conn.execute(
            "INSERT INTO directory (name, parent, description, color, sort_order) "
            "SELECT name, '', description, color, sort_order FROM directory_old"
        )
        conn.execute("DROP TABLE directory_old")


def insert_feed(conn: sqlite3.Connection, url: str, name: str, description: str) -> int:
    """通用插入订阅源(已存在则跳过)。返回新增行数(1/0)。装机与其他写入方共用。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO feed (url, name, description) VALUES (?,?,?)",
        (url, name, description),
    )
    return cur.rowcount


def insert_directory(
    conn: sqlite3.Connection, name: str, parent: str, description: str, color: str, sort_order: int
) -> int:
    """通用插入分类(已存在则跳过)。返回新增行数(1/0)。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO directory (name, parent, description, color, sort_order) "
        "VALUES (?,?,?,?,?)",
        (name, parent, description, color, sort_order),
    )
    return cur.rowcount


def insert_setting(conn: sqlite3.Connection, key: str, value: str) -> int:
    """通用插入配置项(已存在则跳过,不覆盖用户已改的值)。返回新增行数(1/0)。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (key, value)
    )
    return cur.rowcount


def upsert_source(
    conn: sqlite3.Connection,
    source_id: str,
    name: str,
    tier: str,
    category: str | None = None,
) -> None:
    """Register a feed observed during ingest, so the viewer can list it."""
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
