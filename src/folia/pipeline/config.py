from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# repo root: src/folia/pipeline/config.py → up 3 (pipeline → folia → src → root)
ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class SourceMeta:
    name: str | None
    tier: str
    category: str


@dataclass(frozen=True)
class SourceMap:
    """tier/category lookup for FreshRSS feeds, keyed by streamId and origin title."""

    by_stream_id: dict[str, SourceMeta]
    by_title: dict[str, SourceMeta]

    def resolve(self, stream_id: str | None, title: str | None) -> SourceMeta:
        if stream_id and stream_id in self.by_stream_id:
            return self.by_stream_id[stream_id]
        if title and title in self.by_title:
            return self.by_title[title]
        return SourceMeta(name=None, tier="unknown", category="uncategorized")


def load_toml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("rb") as file:
        return tomllib.load(file)


def load_settings(path: str | Path = "config/settings.toml") -> dict[str, Any]:
    return load_toml(ROOT / path)


def load_source_map(path: str | Path = "config/sources.toml") -> SourceMap:
    raw = load_toml(ROOT / path)
    by_stream_id: dict[str, SourceMeta] = {}
    by_title: dict[str, SourceMeta] = {}
    for item in raw.get("sources", []):
        meta = SourceMeta(
            name=item.get("name"),
            tier=item.get("tier", "unknown"),
            category=item.get("category", "uncategorized"),
        )
        if item.get("stream_id"):
            by_stream_id[str(item["stream_id"])] = meta
        if item.get("match"):
            by_title[str(item["match"])] = meta
    return SourceMap(by_stream_id=by_stream_id, by_title=by_title)


def database_path(settings: dict[str, Any]) -> Path:
    configured = settings.get("database", {}).get("path", "data/frontpage.sqlite")
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
