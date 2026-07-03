"""配置全部存 SQLite(settings / source_map 表), 由面板编辑。

- 只有 db 路径是引导项(env FOLIA_DB_PATH 或默认), 因为读 db 前得先知道 db 在哪。
- 其余运行期配置: 内置默认(_defaults) + db `settings` 表的点分键覆盖 → 还原成既有的嵌套 dict,
  消费者(freshrss_client / embeddings / dedupe / model_client)照旧读 dict, 不用改读法。
- URL 类默认读环境变量(容器用 compose env: freshrss / host.docker.internal; 宿主用 localhost)。
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# repo root: src/folia/pipeline/config.py → 上 3 层
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


# 默认订阅种子(原 subscriptions.opml): (feed_url, 显示名, 分组)。面板"导入默认订阅"用;
# fresh 环境 feed_seed 表为空时回退到这里。URL 是经 fulltextrss 包装的全文源。
DEFAULT_FEEDS: list[tuple[str, str, str]] = [
    ("http://fulltextrss/makefulltextfeed.php?url=https%3A%2F%2Ffeeds.apnews.com%2Frss%2Fapf-topnews&max=20", "AP News", "Wire"),
    ("http://fulltextrss/makefulltextfeed.php?url=https%3A%2F%2Fwww.reutersagency.com%2Ffeed%2F%3Fbest-topics%3Dworld%26post_type%3Dbest&max=20", "Reuters World", "Wire"),
    ("http://fulltextrss/makefulltextfeed.php?url=https%3A%2F%2Fwww.theguardian.com%2Fworld%2Frss&max=20", "Guardian World", "Broadsheet"),
    ("http://fulltextrss/makefulltextfeed.php?url=https%3A%2F%2Ffeeds.bbci.co.uk%2Fnews%2Fworld%2Frss.xml&max=20", "BBC World", "Broadsheet"),
    ("http://fulltextrss/makefulltextfeed.php?url=https%3A%2F%2Fhnrss.org%2Ffrontpage&max=20", "Hacker News", "Interest"),
    ("http://fulltextrss/makefulltextfeed.php?url=http%3A%2F%2Frsshub%3A1200%2Flatepost&max=20", "LatePost", "CN"),
]


def _defaults() -> dict[str, Any]:
    return {
        "database": {"url": os.environ.get("DATABASE_URL", "")},  # 入库目标(Neon); 空=不入库
        "freshrss": {
            "api_url": os.environ.get("FRESHRSS_API_URL", "http://localhost:8080/api/greader.php"),
            "user": "",
            "api_password": "",
            "batch_size": 100,
            "timeout_seconds": 30,
            "mark_read": False,
        },
        "embeddings": {
            "url": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            "model": "bge-m3",
            "timeout_seconds": 30,
        },
        "dedupe": {
            "same_event_threshold": 0.85,
            "jaccard_threshold": 0.42,
            "lookback_hours": 48,
        },
        "model": {
            "provider": "heuristic",
            "timeout_seconds": 60,
            "temperature": 0.2,
            "max_output_tokens": 3000,
            "openai": {"model": "gpt-4.1-mini", "api_key_env": "OPENAI_API_KEY", "endpoint": "https://api.openai.com/v1/responses"},
            "claude": {"model": "claude-3-5-haiku-latest", "api_key_env": "ANTHROPIC_API_KEY", "endpoint": "https://api.anthropic.com/v1/messages"},
            "gemini": {"model": "gemini-1.5-flash", "api_key_env": "GEMINI_API_KEY", "endpoint": "https://generativelanguage.googleapis.com/v1beta"},
            "xinapi": {"model": "deepseek-ai/DeepSeek-R1", "api_key_env": "XIN_API_KEY", "endpoint": "https://airouter.xincache.cn/v1/chat/completions"},
        },
        "loop": {"enabled": False, "interval": 1800},
    }


def load_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    settings = _defaults()
    for row in conn.execute("SELECT key, value FROM settings"):
        _apply_dotted(settings, str(row[0]), row[1])
    return settings


def _apply_dotted(tree: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            return  # 未知路径, 忽略
        node = child
    leaf = parts[-1]
    node[leaf] = _coerce(node.get(leaf), value)


def _coerce(default: Any, value: Any) -> Any:
    if value is None:
        return default
    text = str(value)
    if isinstance(default, bool):
        return text.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(text)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(text)
        except ValueError:
            return default
    return text


def database_path() -> Path:
    configured = os.environ.get("FOLIA_DB_PATH", "data/frontpage.sqlite")
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_source_map(conn: sqlite3.Connection) -> SourceMap:
    by_stream_id: dict[str, SourceMeta] = {}
    by_title: dict[str, SourceMeta] = {}
    for row in conn.execute(
        "SELECT match_type, match_key, name, tier, category FROM source_map"
    ):
        meta = SourceMeta(
            name=row["name"],
            tier=row["tier"] or "unknown",
            category=row["category"] or "uncategorized",
        )
        if row["match_type"] == "stream_id":
            by_stream_id[str(row["match_key"])] = meta
        else:
            by_title[str(row["match_key"])] = meta
    return SourceMap(by_stream_id=by_stream_id, by_title=by_title)
