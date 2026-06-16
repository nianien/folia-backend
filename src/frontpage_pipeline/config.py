from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .models import Source


ROOT = Path(__file__).resolve().parents[2]


def load_toml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("rb") as file:
        return tomllib.load(file)


def load_settings(path: str | Path = "config/settings.toml") -> dict[str, Any]:
    return load_toml(ROOT / path)


def load_sources(path: str | Path = "config/sources.toml") -> list[Source]:
    raw = load_toml(ROOT / path)
    return [
        Source(
            id=item["id"],
            name=item["name"],
            url=item["url"],
            tier=item["tier"],
            category_hint=item.get("category_hint"),
            enabled=bool(item.get("enabled", True)),
        )
        for item in raw.get("sources", [])
    ]


def database_path(settings: dict[str, Any]) -> Path:
    configured = settings.get("database", {}).get("path", "data/frontpage.sqlite")
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
