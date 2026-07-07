"""配置全部存 SQLite(settings / feed 表), 由面板编辑。

- 只有 db 路径是引导项(env FOLIA_DB_PATH 或默认), 因为读 db 前得先知道 db 在哪。
- 其余运行期配置: 内置默认(_defaults) + db `settings` 表的点分键覆盖 → 还原成既有的嵌套 dict,
  消费者(poller / embeddings / dedupe / model_client)照旧读 dict, 不用改读法。
- URL 类默认读环境变量(容器用 compose env: host.docker.internal; 宿主用 localhost)。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

# repo root: src/folia/pipeline/config.py → 上 3 层
ROOT = Path(__file__).resolve().parents[3]

# 默认订阅源: (feed_url, 名称, 一句话描述)。feed 表为空时播种(db.seed_default_feeds)。
# 原始 RSS/Atom 地址(自写轮询器直接抓, 全文交给 trafilatura)。分类由内容决定, 不挂在源上。
DEFAULT_FEEDS: list[tuple[str, str, str]] = [
    ("http://rsshub:1200/apnews/topics/apf-topnews", "AP News", "美联社,国际通讯社头条快讯"),
    ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera", "半岛电视台,国际新闻"),
    ("https://www.theguardian.com/world/rss", "Guardian World", "《卫报》国际版"),
    ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World", "BBC 世界新闻"),
    ("https://hnrss.org/frontpage", "Hacker News", "科技创业社区热门讨论"),
    ("http://rsshub:1200/latepost", "LatePost", "晚点 LatePost,中文科技与商业报道"),
]

# 默认新闻分类: (名称, 父级, 描述, 颜色, 排序)。父级 "" = 一级; 否则 = 所属一级名。
# 只播一级; 二级由用户在「新闻分类」页按需加。分类结果可停在一级(归不到二级时)或到二级。
DEFAULT_DIRECTORIES: list[tuple[str, str, str, str, int]] = [
    ("国际", "", "国际 / 世界新闻", "#0f9d76", 1),
    ("科技", "", "科技 / 互联网 / AI", "#1f8fb3", 2),
    ("中国", "", "中国相关", "#2a9d8f", 3),
    ("综合", "", "综合 / 未归类", "#6d7c75", 99),
]

FALLBACK_CATEGORY = "综合"  # 彻底归不了时落这个一级

# 支持的 LLM 供应商(下拉顺序)。ollama 是本地(无 key)。openai/deepseek/qwen/xinapi 走
# OpenAI 兼容 chat/completions; claude 走 messages; gemini 走 generateContent。
# (显示名, 默认 endpoint, 历史 API key 环境变量名)。key 默认回退该环境变量, 配置页可覆盖。
PROVIDERS: list[tuple[str, str, str, str]] = [
    ("ollama", "Ollama(本地)", os.environ.get("OLLAMA_URL", "http://localhost:11434"), ""),
    ("openai", "OpenAI", "https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    ("claude", "Claude", "https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY"),
    ("gemini", "Gemini", "https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY"),
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1/chat/completions", "DEEPSEEK_API_KEY"),
    ("qwen", "通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "DASHSCOPE_API_KEY"),
    ("xinapi", "XinAPI", "https://airouter.xincache.cn/v1/chat/completions", "XIN_API_KEY"),
]

# 各 provider 的预置候选模型(面板下拉用; 只是建议, 可直接填任意名)。
# 精简为当前主力型号: 便宜快的做事实抽取, 中高档做成稿, 旗舰做质量对照。
# xinapi 是中转/聚合渠道, 列的是它转发的当前型号名(注意: 同名未必是原厂同版本)。
PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-4.1-mini", "gpt-5.4-mini"],
    "claude": ["claude-haiku-4-5", "claude-sonnet-5"],
    "gemini": ["gemini-3.5-flash"],
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
    "qwen": ["qwen3.6-flash", "qwen3.7-plus"],
    "xinapi": ["gpt-5.4-mini", "claude-sonnet-5", "gemini-3.5-flash"],
    "ollama": ["qwen3.6:35b-a3b", "qwen3.5:9b", "qwen3:14b", "gemma4:12b", "gemma3:4b"],
}

# embedding 固定本地 Ollama 的预置候选(嵌入模型, 与 chat 模型不同)。
EMBED_MODELS: list[str] = ["bge-m3", "nomic-embed-text", "mxbai-embed-large"]


def _defaults() -> dict[str, Any]:
    return {
        "database": {"url": os.environ.get("DATABASE_URL", "")},  # 入库目标(Neon); 空=不入库
        "poller": {
            "timeout_seconds": 20,  # 每个源抓取超时
        },
        "embeddings": {
            "url": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            "timeout_seconds": 30,
        },
        "dedupe": {
            "same_event_threshold": 0.85,
            "jaccard_threshold": 0.42,
            "lookback_hours": 48,
        },
        "model": {  # LLM 通用参数(所有 provider 共用)
            "timeout_seconds": 120,
            "temperature": 0.2,
            "max_output_tokens": 3000,
            "num_ctx": 8192,  # 仅本地 Ollama 生效; 不设则默认开 32K, 大模型在小内存机上会溢出到 swap
        },
        # 各供应商的 endpoint 与 API key; key 默认取历史环境变量, 配置页可覆盖并落库。
        "providers": {
            name: {
                "endpoint": endpoint,
                "api_key": os.environ.get(key_env, "") if key_env else "",
            }
            for name, _label, endpoint, key_env in PROVIDERS
        },
        # 各功能选 provider + 模型。默认全走本地 Ollama(这是个 AI 项目, 最低也用本地模型)。
        "models": {
            "embedding": "bge-m3",
            "categorize": {"provider": "ollama", "model": "qwen3.5:9b"},
            "synthesis": {"provider": "ollama", "model": "qwen3.5:9b"},
            "facts": {"provider": "ollama", "model": "qwen3.5:9b"},
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


def is_pg_dsn(dsn: str) -> bool:
    """只允许 postgres 连接串(挡 SSRF: 别的 scheme 一律拒)。"""
    return dsn.startswith("postgres://") or dsn.startswith("postgresql://")


def database_path() -> Path:
    configured = os.environ.get("FOLIA_DB_PATH", "data/frontpage.sqlite")
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
