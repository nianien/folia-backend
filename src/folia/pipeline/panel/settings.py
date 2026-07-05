"""面板对 db 配置的读写。

- settings 表: 点分键(如 embeddings.url / dedupe.same_event_threshold / loop.interval),
  由 config.load_settings 还原成嵌套 dict。这里只做 get / set_many。
- feed 表: 订阅源(本地即真身, 轮询器直接抓)。
- source_map 表: 数据源的 tier/category 映射(面板"数据源"页管理)。
"""
from __future__ import annotations

import sqlite3


def get(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row is not None and row[0] is not None else ""


def set_many(conn: sqlite3.Connection, values: dict[str, str]) -> None:
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    conn.commit()


def list_source_map(conn: sqlite3.Connection) -> list[dict]:
    return [
        {
            "match_type": r[0],
            "match_key": r[1],
            "name": r[2],
            "tier": r[3],
            "category": r[4],
        }
        for r in conn.execute(
            "SELECT match_type, match_key, name, tier, category FROM source_map "
            "ORDER BY match_type, match_key"
        )
    ]


def set_source_map(
    conn: sqlite3.Connection, match_type: str, match_key: str, name: str, tier: str, category: str
) -> None:
    conn.execute(
        "INSERT INTO source_map (match_type, match_key, name, tier, category) "
        "VALUES (?,?,?,?,?) ON CONFLICT(match_type, match_key) DO UPDATE SET "
        "name=excluded.name, tier=excluded.tier, category=excluded.category",
        (match_type, match_key, name, tier, category),
    )
    conn.commit()


def delete_source_map(conn: sqlite3.Connection, match_type: str, match_key: str) -> None:
    conn.execute(
        "DELETE FROM source_map WHERE match_type=? AND match_key=?", (match_type, match_key)
    )
    conn.commit()


def list_feeds(conn: sqlite3.Connection) -> list[dict]:
    """订阅源列表(feed 表就是真身)。"""
    return [
        {
            "url": r[0],
            "title": r[1],
            "tier": r[2],
            "category": r[3],
            "last_status": r[4],
            "last_fetched_at": r[5],
            "enabled": bool(r[6]),
        }
        for r in conn.execute(
            "SELECT url, title, tier, category, last_status, last_fetched_at, enabled "
            "FROM feed ORDER BY category, title"
        )
    ]


def add_feed(
    conn: sqlite3.Connection, url: str, title: str = "", tier: str = "", category: str = ""
) -> None:
    conn.execute(
        "INSERT INTO feed (url, title, tier, category) VALUES (?,?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET title=excluded.title, tier=excluded.tier, "
        "category=excluded.category",
        (url, title, tier, category),
    )
    conn.commit()


def remove_feed(conn: sqlite3.Connection, url: str) -> None:
    conn.execute("DELETE FROM feed WHERE url=?", (url,))
    conn.commit()


def import_default_feeds(conn: sqlite3.Connection) -> int:
    """把内置默认订阅(config.DEFAULT_FEEDS)并入 feed 表(已存在的跳过)。返回新增数。"""
    from ..config import DEFAULT_FEEDS

    added = 0
    for url, title, tier, category in DEFAULT_FEEDS:
        cur = conn.execute(
            "INSERT OR IGNORE INTO feed (url, title, tier, category) VALUES (?,?,?,?)",
            (url, title, tier, category),
        )
        added += cur.rowcount
    conn.commit()
    return added
