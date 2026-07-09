#!/usr/bin/env python3
"""一次性初始化 SQLite 库:建表 + 写入初始数据(订阅源 / 分类 / 运行期配置)。

- 复用 db.py 的 SCHEMA/init_db 建表(不重复维护 DDL),再调 db 的通用插入方法写默认数据。
- 幂等且非破坏:全部 INSERT OR IGNORE,只补缺失的行/键,已有配置一律保留。
- 运行期代码(config / db / panel / poller ...)**不引用本文件**;默认数据只活在这里。
  之后一切以数据库为唯一真相;面板负责读 DB 显示、页面增删改。

库路径取 config.database_path()(env FOLIA_DB_PATH 或默认 data/frontpage.sqlite)。

用法:
    python scripts/init_db.py                        # 初始化 ./data/frontpage.sqlite
    FOLIA_DB_PATH=/tmp/x.sqlite python scripts/init_db.py
    容器启动会自动跑一次(见 Dockerfile CMD);面板「一键初始化」按钮同样触发它。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 允许未安装包时直接跑: 把 src/ 加进导入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from folia.pipeline.config import PROVIDERS, database_path
from folia.pipeline.db import connect, init_db, insert_directory, insert_feed, insert_setting


# 默认订阅源: (feed_url, 名称, 一句话描述)。原始 RSS/Atom;分类由内容决定,不挂在源上。
DEFAULT_FEEDS: list[tuple[str, str, str]] = [
    ("http://rsshub:1200/apnews/topics/apf-topnews", "AP News", "美联社,国际通讯社头条快讯"),
    ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera", "半岛电视台,国际新闻"),
    ("https://www.theguardian.com/world/rss", "Guardian World", "《卫报》国际版"),
    ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World", "BBC 世界新闻"),
    ("https://hnrss.org/frontpage", "Hacker News", "科技创业社区热门讨论"),
    ("http://rsshub:1200/latepost", "LatePost", "晚点 LatePost,中文科技与商业报道"),
]

# 默认新闻分类: (名称, 父级, 描述, 颜色, 排序)。父级 "" = 一级。只播一级,二级用户按需加。
DEFAULT_DIRECTORIES: list[tuple[str, str, str, str, int]] = [
    ("国际", "", "国际 / 世界新闻", "#0f9d76", 1),
    ("科技", "", "科技 / 互联网 / AI", "#1f8fb3", 2),
    ("中国", "", "中国相关", "#2a9d8f", 3),
    ("综合", "", "综合 / 未归类", "#6d7c75", 99),
]

# 本地 Ollama 地址: 写死成容器该用的地址(compose 里 panel 经 host.docker.internal 访问宿主)。
OLLAMA_URL = "http://host.docker.internal:11434"


def default_settings() -> dict:
    """运行期通用配置的初值(嵌套 dict,写入时拍平成点分键存进 settings 表)。"""
    providers = {
        name: {
            "endpoint": OLLAMA_URL if name == "ollama" else endpoint,
            "api_key": os.environ.get(key_env, "") if key_env else "",
        }
        for name, _label, endpoint, key_env in PROVIDERS
    }
    return {
        "database": {"url": os.environ.get("DATABASE_URL", "")},
        "poller": {"timeout_seconds": 20},
        "embeddings": {"url": OLLAMA_URL, "timeout_seconds": 30},
        "dedupe": {"same_event_threshold": 0.85, "jaccard_threshold": 0.42, "lookback_hours": 48},
        "model": {"timeout_seconds": 120, "temperature": 0.2, "max_output_tokens": 3000, "num_ctx": 8192},
        "providers": providers,
        "models": {
            "embedding": "bge-m3",
            "categorize": {"provider": "ollama", "model": "qwen3.6:35b-a3b"},
            "synthesis": {"provider": "ollama", "model": "qwen3.6:35b-a3b"},
            "facts": {"provider": "ollama", "model": "qwen3.6:35b-a3b"},
        },
        "loop": {"enabled": False, "interval": 1800},
    }


def _flatten(tree: dict, prefix: str = "") -> list[tuple[str, str]]:
    """嵌套 dict → [(点分键, 字符串值)]。bool 存 '1'/'0',其余 str()。"""
    out: list[tuple[str, str]] = []
    for key, value in tree.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.extend(_flatten(value, dotted))
        else:
            out.append((dotted, "1" if value is True else "0" if value is False else str(value)))
    return out


def main() -> int:
    path = database_path()
    conn = connect(path)
    try:
        init_db(conn)  # 只建表 + 迁移,不含任何数据
        directories = sum(insert_directory(conn, *row) for row in DEFAULT_DIRECTORIES)
        feeds = sum(insert_feed(conn, url, name, desc) for url, name, desc in DEFAULT_FEEDS)
        settings = sum(insert_setting(conn, k, v) for k, v in _flatten(default_settings()))
        conn.commit()
    finally:
        conn.close()
    print(f"✓ initialized {path}")
    print(f"  directories +{directories}, feeds +{feeds}, settings +{settings}  (已存在的跳过)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
