from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    tier: str
    category_hint: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class FeedArticle:
    source_id: str
    source_name: str
    source_tier: str
    title: str
    url: str
    guid: str | None
    published_at: str | None
    summary: str | None


@dataclass(frozen=True)
class ArticleRecord:
    id: str
    source_id: str
    source_name: str
    source_tier: str | None
    title: str
    url: str
    canonical_url: str | None
    summary: str | None
    published_at: str | None
    extracted_text: str | None
    article_facts: str | None
    cluster_id: int | None
