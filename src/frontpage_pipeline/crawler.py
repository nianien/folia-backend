from __future__ import annotations

import urllib.parse
import urllib.request


def is_paywalled(url: str, domains: list[str]) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def crawl(url: str, timeout: int, user_agent: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("content-type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        payload = response.read()
    if "html" not in content_type and content_type:
        raise RuntimeError(f"unsupported content type: {content_type}")
    return payload.decode(charset, errors="replace")
