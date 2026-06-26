#!/usr/bin/env bash
# 入库: 把最新聚合结果导出并写入 Postgres(Neon)。
# 前提: 已 run-once 生成本地数据; .env 里有 DATABASE_URL; 已装本包(含 psycopg)。
# 用法: ./scripts/publish.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# .env 的 DATABASE_URL 含 & , 不能 source; 直接取值注入
export DATABASE_URL="$(grep '^DATABASE_URL=' .env | head -1 | cut -d= -f2-)"
export PYTHONPATH=src

echo "==> 导出 frontpage.json"
python -m folia.pipeline.cli export

echo "==> 入库到 Neon"
python -m folia.pipeline.cli load

echo ""
echo "完成: 聚合数据已写入 Neon。"
