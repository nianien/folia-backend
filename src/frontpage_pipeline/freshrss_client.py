from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import SourceMap
from .models import FeedArticle
from .text import clean_text

READING_LIST = "user/-/state/com.google/reading-list"
READ_STATE = "user/-/state/com.google/read"


class FreshRSSError(RuntimeError):
    pass


@dataclass(frozen=True)
class FreshRSSConfig:
    api_url: str
    user: str
    api_password: str
    timeout_seconds: int
    batch_size: int
    mark_read: bool


class FreshRSSClient:
    def __init__(self, config: FreshRSSConfig) -> None:
        self.config = config
        self._auth: str | None = None
        self._token: str | None = None

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "FreshRSSClient":
        raw = settings.get("freshrss", {})
        api_url = os.environ.get("FRESHRSS_API_URL") or raw.get("api_url")
        user = os.environ.get("FRESHRSS_USER", "")
        password = os.environ.get("FRESHRSS_API_PASSWORD", "")
        if not api_url:
            raise FreshRSSError("FRESHRSS_API_URL is not configured")
        if not user or not password:
            raise FreshRSSError("FRESHRSS_USER / FRESHRSS_API_PASSWORD are not set")
        return cls(
            FreshRSSConfig(
                api_url=api_url.rstrip("/"),
                user=user,
                api_password=password,
                timeout_seconds=int(raw.get("timeout_seconds", 30)),
                batch_size=int(raw.get("batch_size", 100)),
                mark_read=bool(raw.get("mark_read", False)),
            )
        )

    def login(self) -> str:
        body = urllib.parse.urlencode(
            {"Email": self.config.user, "Passwd": self.config.api_password}
        ).encode("utf-8")
        raw = self._request(f"{self.config.api_url}/accounts/ClientLogin", data=body)
        for line in raw.splitlines():
            if line.startswith("Auth="):
                self._auth = line[len("Auth=") :].strip()
                return self._auth
        raise FreshRSSError("ClientLogin response did not contain an Auth token")

    def iter_unread(self, count: int | None = None) -> Iterator[dict]:
        if self._auth is None:
            self.login()
        limit = count if count is not None else None
        yielded = 0
        continuation: str | None = None
        base = f"{self.config.api_url}/reader/api/0/stream/contents/{READING_LIST}"
        while True:
            params = {
                "output": "json",
                "xt": READ_STATE,
                "n": str(self.config.batch_size),
            }
            if continuation:
                params["c"] = continuation
            url = f"{base}?{urllib.parse.urlencode(params)}"
            data = json.loads(self._request(url, headers=self._auth_header()))
            for item in data.get("items", []):
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            continuation = data.get("continuation")
            if not continuation:
                return

    def mark_read(self, item_ids: list[str]) -> None:
        if not self.config.mark_read or not item_ids:
            return
        token = self._get_token()
        for chunk in _chunks(item_ids, 100):
            pairs = [("i", item_id) for item_id in chunk]
            pairs.append(("a", READ_STATE))
            pairs.append(("T", token))
            body = urllib.parse.urlencode(pairs).encode("utf-8")
            self._request(
                f"{self.config.api_url}/reader/api/0/edit-tag",
                data=body,
                headers=self._auth_header(),
            )

    def _auth_header(self) -> dict[str, str]:
        if self._auth is None:
            self.login()
        return {"Authorization": f"GoogleLogin auth={self._auth}"}

    def _get_token(self) -> str:
        if self._token is None:
            self._token = self._request(
                f"{self.config.api_url}/reader/api/0/token", headers=self._auth_header()
            ).strip()
        return self._token

    def _request(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        request = urllib.request.Request(url, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise FreshRSSError(f"FreshRSS HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise FreshRSSError(f"FreshRSS request failed: {exc}") from exc


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _item_url(item: dict) -> str | None:
    for key in ("canonical", "alternate"):
        links = item.get(key)
        if isinstance(links, list):
            for link in links:
                href = link.get("href") if isinstance(link, dict) else None
                if href:
                    return href
    return None


def _published_iso(value: Any) -> str | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def freshrss_item_to_article(item: dict, source_map: SourceMap) -> FeedArticle | None:
    title = clean_text(item.get("title"))
    url = _item_url(item)
    if not title or not url:
        return None
    origin = item.get("origin", {}) if isinstance(item.get("origin"), dict) else {}
    stream_id = origin.get("streamId")
    origin_title = clean_text(origin.get("title"))
    meta = source_map.resolve(stream_id, origin_title)
    content_html = ""
    summary = item.get("summary")
    if isinstance(summary, dict):
        content_html = summary.get("content") or ""
    external_id = item.get("id")
    return FeedArticle(
        source_id=stream_id or external_id or url,
        source_name=meta.name or origin_title or "unknown",
        source_tier=meta.tier,
        category=meta.category,
        title=title,
        url=url,
        guid=external_id,
        published_at=_published_iso(item.get("published")),
        summary=clean_text(content_html) or None,
        content_html=content_html or None,
        external_id=external_id,
    )
