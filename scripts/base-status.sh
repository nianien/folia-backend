#!/usr/bin/env bash
# 查看基座层状态与日志。用法:./scripts/base-status.sh [服务名]
set -euo pipefail
export PATH="$HOME/.orbstack/bin:$PATH"
cd "$(dirname "$0")/.."

docker compose ps
echo ""
if [ "${1:-}" != "" ]; then
  echo "==> $1 最近 50 行日志"
  docker compose logs --tail=50 "$1"
fi
