#!/usr/bin/env bash
# 停止基座层。用法:./scripts/base-down.sh [--volumes]
# --volumes 同时删除 freshrss_data 卷(清空订阅/账户,谨慎)。
set -euo pipefail
export PATH="$HOME/.orbstack/bin:$PATH"
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--volumes" ]; then
  docker compose down -v
  echo "已停止并删除数据卷。"
else
  docker compose down
  echo "已停止(数据卷保留;--volumes 可一并删除)。"
fi
