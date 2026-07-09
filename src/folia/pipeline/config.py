"""运行期只读配置:全部从 SQLite 的 `settings` 表读,不含任何内置默认。

- 只有 db 路径是引导项(env FOLIA_DB_PATH 或默认), 因为读 db 前得先知道 db 在哪。
- 初始数据(feeds / directories / settings)由一次性的 `scripts/init_db.py` 写入;本模块不引用它,
  运行期也没有任何"默认兜底"——库里有什么就读什么, 消费者各自带内联默认应对缺键。
- `settings` 表存点分键(如 dedupe.jaccard_threshold), load_settings 还原成嵌套 dict;
  值是字符串(SQLite TEXT), 消费者读时自行 int()/float()/truthy() 转型。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

# repo root: src/folia/pipeline/config.py → 上 3 层
ROOT = Path(__file__).resolve().parents[3]

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


def load_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """只读 settings 表, 把点分键还原成嵌套 dict。叶子为字符串, 消费者自行转型。

    库里没有的键就不存在(无兜底默认); 消费者用 .get(key, 内联默认) 应对。
    """
    settings: dict[str, Any] = {}
    for row in conn.execute("SELECT key, value FROM settings"):
        _apply_dotted(settings, str(row[0]), "" if row[1] is None else str(row[1]))
    return settings


def _apply_dotted(tree: dict[str, Any], dotted: str, value: str) -> None:
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def truthy(value: Any) -> bool:
    """把 settings 里的字符串布尔值('1'/'true'/…)转成 bool。"""
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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
