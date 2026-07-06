#!/usr/bin/env python3
"""初始化 SQLite 库: 建表 + 播种默认目录 + 播种默认订阅源。

复用 db.py 的 SCHEMA/init_db, 不重复维护 DDL。幂等:
- 表用 CREATE TABLE IF NOT EXISTS;
- 目录/订阅仅在各自表为空时播种(seed_default_*), 重复跑不会重复插。

库路径取 config.database_path()(env FOLIA_DB_PATH 或默认 data/frontpage.sqlite)。

用法:
    python scripts/init_db.py
    FOLIA_DB_PATH=/tmp/x.sqlite python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许未安装包时直接跑: 把 src/ 加进导入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from folia.pipeline.config import database_path
from folia.pipeline.db import connect, init_db, seed_default_feeds


def main() -> int:
    path = database_path()
    conn = connect(path)
    try:
        init_db(conn)                        # 建表 + 播种默认目录
        seeded_feeds = seed_default_feeds(conn)  # 空表时播种默认订阅
        dirs = conn.execute("SELECT COUNT(*) FROM directory").fetchone()[0]
        feeds = conn.execute("SELECT COUNT(*) FROM feed").fetchone()[0]
    finally:
        conn.close()
    print(f"✓ initialized {path}")
    print(f"  directories: {dirs}")
    print(f"  feeds: {feeds} (+{seeded_feeds} seeded this run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
