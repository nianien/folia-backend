"""本地 Ollama 工具: 列出已装模型(供「模型」页下拉用)。"""
from __future__ import annotations

import json
import urllib.request


def list_models(base_url: str, timeout: int = 5) -> list[str]:
    """读 /api/tags 返回已装模型名(排序); 连不上/出错返回空表。"""
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))
