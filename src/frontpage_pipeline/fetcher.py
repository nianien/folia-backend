from __future__ import annotations

import email.utils
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterable

from .models import FeedArticle, Source
from .text import clean_text


def fetch_url(url: str, timeout: int, user_agent: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return clean_text(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def child_text(element: ET.Element, names: Iterable[str]) -> str | None:
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return clean_text(found.text)
    for child in element:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag in names and child.text:
            return clean_text(child.text)
    return None


def item_link(element: ET.Element) -> str | None:
    link = child_text(element, ["link"])
    if link:
        return link
    for child in element:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "link":
            href = child.attrib.get("href")
            if href:
                return href
    return None


def parse_feed(payload: bytes, source: Source) -> list[FeedArticle]:
    root = ET.fromstring(payload)
    root_tag = root.tag.rsplit("}", 1)[-1].lower()
    if root_tag == "rss":
        items = root.findall("./channel/item")
    else:
        items = [item for item in root if item.tag.rsplit("}", 1)[-1] == "entry"]

    articles: list[FeedArticle] = []
    for item in items:
        title = child_text(item, ["title"])
        url = item_link(item)
        if not title or not url:
            continue
        guid = child_text(item, ["guid", "id"])
        published = child_text(item, ["pubDate", "published", "updated"])
        summary = child_text(item, ["description", "summary", "content"])
        articles.append(
            FeedArticle(
                source_id=source.id,
                source_name=source.name,
                source_tier=source.tier,
                title=title,
                url=url,
                guid=guid,
                published_at=parse_datetime(published),
                summary=summary,
            )
        )
    return articles


def fetch_source(source: Source, timeout: int, user_agent: str) -> list[FeedArticle]:
    try:
        return parse_feed(fetch_url(source.url, timeout, user_agent), source)
    except (ET.ParseError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"failed to fetch {source.id}: {exc}") from exc
