from __future__ import annotations

import hashlib
import html
import re
import urllib.parse


WORD_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [
        (key, value)
        for key, value in query
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    normalized_query = urllib.parse.urlencode(filtered)
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower() or "https",
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            normalized_query,
            "",
        )
    )


def stable_id(*parts: str | None) -> str:
    payload = "\n".join(part or "" for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def content_hash(*parts: str | None) -> str:
    payload = clean_text(" ".join(part or "" for part in parts)).lower()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tokenize(value: str | None) -> set[str]:
    words = WORD_RE.findall(clean_text(value).lower())
    return {word for word in words if len(word) > 2}


def jaccard(left: str | None, right: str | None) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
