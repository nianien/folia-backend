"""自写轮询抓取器。

- feed 表就是订阅真身(本地即真身), 无账号/无密码/无外部 API。
- 每轮遍历 enabled 的源: 条件请求(带上轮 ETag/Last-Modified)+ 自定义 UA + 超时,
  feedparser 解析 → 每条 entry 转 FeedArticle → 复用 insert_article 去重入库。
- 坏源 try/except 单独兜住, 不拖垮整轮; 回写 last_status / etag / modified。
- 全文不在这里抽: entry 自带的正文落 content_html, 后续 extractor(trafilatura)按需补全文。
"""
from __future__ import annotations

import socket
import sqlite3
from datetime import datetime, timezone
from typing import Any

import feedparser

from .db import insert_article, upsert_source
from .models import FeedArticle
from .text import clean_text

UA = "folia-pipeline/0.1 (+https://github.com/nianien/folia-backend)"
TIMEOUT_SECONDS = 20


def poll(conn: sqlite3.Connection, settings: dict[str, Any]) -> int:
    """抓取所有 enabled 的源, 返回本轮新入库文章数。源由 scripts/init_db.py 播种 / 面板增删。"""
    timeout = int(settings.get("poller", {}).get("timeout_seconds", TIMEOUT_SECONDS))
    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    total = 0
    try:
        feeds = list(
            conn.execute("SELECT url, name, etag, modified FROM feed WHERE enabled=1")
        )
        for feed in feeds:
            try:
                inserted = _poll_one(conn, feed)
                total += inserted
                _mark(conn, feed["url"], f"ok: +{inserted}")
            except Exception as exc:  # 单源失败不拖垮整轮
                _mark(conn, feed["url"], f"error: {exc}"[:200])
    finally:
        socket.setdefaulttimeout(prev_timeout)
    return total


def _poll_one(conn: sqlite3.Connection, feed: sqlite3.Row) -> int:
    parsed = feedparser.parse(
        feed["url"],
        etag=feed["etag"] or None,
        modified=feed["modified"] or None,
        agent=UA,
    )
    if getattr(parsed, "status", 0) == 304:  # 未变更, 跳过
        return 0
    inserted = 0
    for entry in parsed.entries:
        article = _entry_to_article(entry, feed)
        if article is None:
            continue
        upsert_source(conn, article.source_id, article.source_name, article.source_tier, article.category)
        if insert_article(conn, article):
            inserted += 1
    conn.execute(
        "UPDATE feed SET etag=?, modified=? WHERE url=?",
        (getattr(parsed, "etag", None), getattr(parsed, "modified", None), feed["url"]),
    )
    conn.commit()
    return inserted


def _entry_to_article(entry: Any, feed: sqlite3.Row) -> FeedArticle | None:
    title = clean_text(entry.get("title"))
    url = entry.get("link")
    if not title or not url:
        return None
    guid = entry.get("id") or entry.get("guid") or url
    content_html = ""
    contents = entry.get("content")
    if contents:
        content_html = contents[0].get("value", "") or ""
    if not content_html:
        content_html = entry.get("summary", "") or ""
    summary = clean_text(content_html) or None
    return FeedArticle(
        source_id=feed["url"],
        source_name=feed["name"] or "unknown",
        source_tier="",  # tier 已废弃; 重要性以后从内容算, 不挂在源上
        category="",  # 留空; 由 categorize_pending 按内容(LLM)定目录
        title=title,
        url=url,
        guid=guid,
        published_at=_published_iso(entry),
        summary=summary,
        content_html=content_html or None,
        external_id=guid,
    )


def _published_iso(entry: Any) -> str | None:
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_time:
        return None
    return datetime(*parsed_time[:6], tzinfo=timezone.utc).isoformat()


def _mark(conn: sqlite3.Connection, url: str, status: str) -> None:
    conn.execute(
        "UPDATE feed SET last_fetched_at=?, last_status=? WHERE url=?",
        (datetime.now(timezone.utc).isoformat(), status, url),
    )
    conn.commit()
