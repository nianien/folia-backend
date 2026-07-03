#!/usr/bin/env bash
# folia 一站式脚本: 起停整套(基座层 + 控制面板)。
# 日常操作(配置/启停循环/间隔/数据源/预览)都在控制面板 http://localhost:8000 里。
# 用法: ./scripts/folia.sh <命令>   (help 看全部)
set -euo pipefail

export PATH="$HOME/.orbstack/bin:$PATH"   # OrbStack docker CLI
cd "$(dirname "$0")/.."                     # repo 根

need_docker() { command -v docker >/dev/null 2>&1 || { echo "✗ 找不到 docker(OrbStack 起了吗? open -a OrbStack)" >&2; exit 1; }; }

cmd_start() {
  need_docker
  echo "==> 构建并拉起(基座层 + 控制面板)"
  docker compose up -d --build
  local st
  for svc in rsshub fulltextrss freshrss panel; do
    printf '   %-12s ' "$svc"
    for _ in $(seq 1 60); do
      st=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$svc" '$1==s{print $2}')
      [ "$st" = "running" ] && { echo "running ✓"; break; }
      sleep 2
    done
    [ "${st:-}" = "running" ] || echo "未就绪 ✗ (docker compose logs $svc)"
  done
  echo
  echo "控制面板: http://localhost:8000"
  echo "首次配置(都在面板里点):"
  echo "  1) 浏览器开 http://localhost:8080 建 FreshRSS 账号并开启 Google Reader API"
  echo "  2) 面板 → 配置: 填 FreshRSS 凭据 / DATABASE_URL / 间隔, 测连接"
  echo "  3) 面板 → 数据源: 导入 OPML 或加订阅"
  echo "  4) 面板 → 控制台: 启动循环 (需本机 ollama serve + ollama pull bge-m3)"
}

cmd_stop() { need_docker; docker compose down; echo "已停止(数据在宿主机 ./data, 不丢)。"; }

cmd_status() {
  need_docker
  docker compose ps
  echo
  probe() { curl -s -o /dev/null -m 5 -w "%{http_code}" "$1" 2>/dev/null || echo 000; }
  echo "   控制面板  http://localhost:8000  -> $(probe http://localhost:8000/admin)"
  echo "   FreshRSS   http://localhost:8080  -> $(probe http://localhost:8080)"
  echo "   RSSHub     http://localhost:1200  -> $(probe http://localhost:1200)"
  echo "   Full-Text  http://localhost:8081  -> $(probe http://localhost:8081)"
  echo "   Ollama     http://localhost:11434 -> $(probe http://localhost:11434)"
}

cmd_install() {  # 仅本地开发/跑测试用(容器运行不需要)
  command -v python3 >/dev/null || { echo "✗ 需要 python3" >&2; exit 1; }
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/python -m pip install -U pip
  .venv/bin/python -m pip install -e .
  echo "dev 环境就绪: PYTHONPATH=src .venv/bin/python -m folia.pipeline.cli ..."
}

cmd_help() {
  cat <<'EOF'
folia.sh — 起停整套 (基座层 + 控制面板)

用法: ./scripts/folia.sh <命令>

  start     构建并拉起 基座层 + 控制面板(http://localhost:8000)
  stop      停止整套(数据在 ./data, 不丢)
  status    容器 + 端口探测
  install   本地 dev 环境(venv + pip install -e ., 跑测试用)
  help      本帮助

日常操作都在控制面板里: 配置凭据/间隔、启停循环、立即跑、管数据源、看预览。
EOF
}

case "${1:-help}" in
  start)   shift; cmd_start "$@";;
  stop|down) shift; cmd_stop "$@";;
  status)  shift; cmd_status "$@";;
  install) shift; cmd_install "$@";;
  help|-h|--help) cmd_help;;
  *) echo "未知命令: ${1:-}" >&2; echo; cmd_help; exit 1;;
esac
