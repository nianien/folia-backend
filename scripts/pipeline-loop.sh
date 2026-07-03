#!/usr/bin/env bash
# 容器内常驻循环: 每 PIPELINE_INTERVAL 秒跑一轮 run-once(+入库)。
# 由 docker-compose 的 pipeline 服务调用; 不在宿主机直接跑。
set -u
# 间隔(秒): 位置参数 $1 > 环境变量 PIPELINE_INTERVAL > 默认 1800
INTERVAL="${1:-${PIPELINE_INTERVAL:-1800}}"

echo "pipeline loop 启动: 每 ${INTERVAL}s 一轮"
while true; do
  echo "=== $(date '+%F %T') run-once ==="
  python -m folia.pipeline.cli run-once || echo "run-once 失败, 下轮重试"
  if [ -n "${DATABASE_URL:-}" ]; then
    echo "--- 入库 ---"
    python -m folia.pipeline.cli export && python -m folia.pipeline.cli load || echo "入库失败"
  fi
  sleep "$INTERVAL"
done
