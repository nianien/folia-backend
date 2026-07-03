"""面板对 db 配置的读写。

- settings 表: 点分键(如 freshrss.api_url / dedupe.same_event_threshold / loop.interval),
  由 config.load_settings 还原成嵌套 dict。这里只做 get / set_many。
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


def list_feed_seed(conn: sqlite3.Connection) -> list[dict]:
    """订阅种子: db feed_seed 表, 表空则回退到代码内置默认(config.DEFAULT_FEEDS)。"""
    rows = list(conn.execute("SELECT url, title, category FROM feed_seed ORDER BY category, title"))
    if not rows:
        from ..config import DEFAULT_FEEDS

        return [{"url": u, "title": t, "category": c} for (u, t, c) in DEFAULT_FEEDS]
    return [{"url": r[0], "title": r[1], "category": r[2]} for r in rows]
