#!/usr/bin/env bash
# 把最新 pipeline 结果发布到 Neon:导出 frontpage.json,再用 compose 灌库。
# 前提:已 run-once 生成数据;.env 里有 DATABASE_URL。
# 用法:./scripts/publish.sh
set -euo pipefail

# OrbStack 的 docker CLI 不在默认 PATH
export PATH="$HOME/.orbstack/bin:$PATH"

cd "$(dirname "$0")/.."

echo "==> 导出 frontpage.json(宿主机)"
PYTHONPATH=src python -m frontpage_pipeline.cli export

echo "==> 灌入 Neon(一次性容器)"
docker compose run --rm frontpage-loader

echo "==> 确保 API 常驻"
docker compose up -d frontpage-api

echo ""
echo "完成。接口在 http://localhost:8090(/stories /search /story/{key})"
