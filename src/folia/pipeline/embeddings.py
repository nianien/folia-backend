from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from array import array
from dataclasses import dataclass
from typing import Any


class EmbeddingsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddingConfig:
    url: str
    model: str
    timeout_seconds: int

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "EmbeddingConfig":
        raw = settings.get("embeddings", {})
        url = str(raw.get("url", "http://localhost:11434"))
        return cls(
            url=url.rstrip("/"),
            model=str(raw.get("model", "bge-m3")),
            timeout_seconds=int(raw.get("timeout_seconds", 30)),
        )


def embed(text: str, config: EmbeddingConfig) -> list[float]:
    body = json.dumps({"model": config.model, "prompt": text}).encode("utf-8")
    request = urllib.request.Request(
        f"{config.url}/api/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise EmbeddingsUnavailable(str(exc)) from exc
    vector = data.get("embedding")
    if not isinstance(vector, list) or not vector:
        raise EmbeddingsUnavailable("embedding response was empty")
    return [float(value) for value in vector]


def is_available(config: EmbeddingConfig) -> bool:
    try:
        embed("ping", config)
        return True
    except EmbeddingsUnavailable:
        return False


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def pack_centroid(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack_centroid(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    values = array("f")
    values.frombytes(blob)
    return list(values)


def update_centroid(old: list[float] | None, old_count: int, new: list[float]) -> list[float]:
    if old is None or old_count <= 0:
        return list(new)
    return [(o * old_count + n) / (old_count + 1) for o, n in zip(old, new)]
