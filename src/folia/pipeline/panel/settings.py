"""面板对 db 配置的读写。

- settings 表: 点分键(如 embeddings.url / dedupe.same_event_threshold / loop.interval),
  由 config.load_settings 还原成嵌套 dict。这里只做 get / set_many。
- feed 表: 订阅源(本地即真身, 轮询器直接抓); 只有 名称/地址/描述, 分类由内容决定。
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


def list_feeds(conn: sqlite3.Connection) -> list[dict]:
    """订阅源列表(feed 表就是真身)。"""
    return [
        {
            "url": r[0],
            "name": r[1],
            "description": r[2],
            "last_status": r[3],
            "last_fetched_at": r[4],
            "enabled": bool(r[5]),
        }
        for r in conn.execute(
            "SELECT url, name, description, last_status, last_fetched_at, enabled "
            "FROM feed ORDER BY name"
        )
    ]


def add_feed(
    conn: sqlite3.Connection, url: str, name: str = "", description: str = ""
) -> None:
    conn.execute(
        "INSERT INTO feed (url, name, description) VALUES (?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET name=excluded.name, description=excluded.description",
        (url, name, description),
    )
    conn.commit()


def remove_feed(conn: sqlite3.Connection, url: str) -> None:
    conn.execute("DELETE FROM feed WHERE url=?", (url,))
    conn.commit()


def list_directories(conn: sqlite3.Connection) -> list[dict]:
    """两级分类扁平表(name, parent, ...); parent='' 为一级。"""
    return [
        {"name": r[0], "parent": r[1], "description": r[2], "color": r[3], "sort_order": r[4]}
        for r in conn.execute(
            "SELECT name, parent, description, color, sort_order FROM directory "
            "ORDER BY parent, sort_order, name"
        )
    ]


def list_directory_tree(conn: sqlite3.Connection) -> list[dict]:
    """分组成 [{一级 + subs:[二级...]}]; 驱动「新闻分类」页与预览嵌套 tab。"""
    flat = list_directories(conn)
    tops = [d for d in flat if not d["parent"]]
    for top in tops:
        top["subs"] = [d for d in flat if d["parent"] == top["name"]]
    return tops


def add_directory(
    conn: sqlite3.Connection, name: str, parent: str = "", description: str = "",
    color: str = "#7a6f5c", sort_order: int = 50,
) -> None:
    conn.execute(
        "INSERT INTO directory (name, parent, description, color, sort_order) VALUES (?,?,?,?,?) "
        "ON CONFLICT(parent, name) DO UPDATE SET description=excluded.description, "
        "color=excluded.color, sort_order=excluded.sort_order",
        (name, parent, description, color or "#7a6f5c", sort_order),
    )
    conn.commit()


def remove_directory(conn: sqlite3.Connection, name: str, parent: str = "") -> None:
    conn.execute("DELETE FROM directory WHERE name=? AND parent=?", (name, parent))
    if not parent:  # 删一级 → 连带删其全部二级
        conn.execute("DELETE FROM directory WHERE parent=?", (name,))
    conn.commit()


def import_default_feeds(conn: sqlite3.Connection) -> int:
    """把内置默认订阅(config.DEFAULT_FEEDS)并入 feed 表(已存在的跳过)。返回新增数。"""
    from ..config import DEFAULT_FEEDS

    added = 0
    for url, name, description in DEFAULT_FEEDS:
        cur = conn.execute(
            "INSERT OR IGNORE INTO feed (url, name, description) VALUES (?,?,?)",
            (url, name, description),
        )
        added += cur.rowcount
    conn.commit()
    return added
