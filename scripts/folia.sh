#!/usr/bin/env bash
# folia 一站式脚本: 起停整套(rsshub + 控制面板)。
# 日常操作(配置/启停循环/间隔/数据源/预览)都在控制面板 http://localhost:8000 里。
# 用法: ./scripts/folia.sh <命令>   (help 看全部)
set -euo pipefail

export PATH="$HOME/.orbstack/bin:$PATH"   # OrbStack docker CLI
cd "$(dirname "$0")/.."                     # repo 根

need_docker() { command -v docker >/dev/null 2>&1 || { echo "✗ 找不到 docker(OrbStack 起了吗? open -a OrbStack)" >&2; exit 1; }; }

cmd_start() {
  need_docker
  echo "==> 构建并拉起(rsshub + 控制面板)"
  docker compose up -d --build
  local st
  for svc in rsshub panel; do
    printf '   %-12s ' "$svc"
    for _ in $(seq 1 60); do
      st=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$svc" '$1==s{print $2}')
      [ "$st" = "running" ] && { echo "running ✓"; break; }
      sleep 2
    done
    [ "${st:-}" = "running" ] || echo "未就绪 ✗ (docker compose logs $svc)"
  done
  echo
  echo "控制面板: http://localhost:8000/admin"
  echo "首次上手(都在面板里点):"
  echo "  1) 数据源: 点「导入默认订阅」或加自己的 RSS 地址"
  echo "  2) 模型: 各功能选 provider + 模型(远程 provider 填 API key; embedding 走本地 Ollama)"
  echo "  3) 数据同步: (可选) 填 database.url(Neon)"
  echo "  4) 抓取: 设间隔并「启动循环」(需本机 ollama serve + ollama pull bge-m3)"
}

cmd_stop() { need_docker; docker compose down; echo "已停止(数据在宿主机 ./data, 不丢)。"; }

cmd_status() {
  need_docker
  docker compose ps
  echo
  probe() { curl -s -o /dev/null -m 5 -w "%{http_code}" "$1" 2>/dev/null || echo 000; }
  echo "   控制面板  http://localhost:8000  -> $(probe http://localhost:8000/)"  # 探预览页(不受面板密码影响)
  echo "   RSSHub     http://localhost:1200  -> $(probe http://localhost:1200)"
  echo "   Ollama     http://localhost:11434 -> $(probe http://localhost:11434)"
}

cmd_install() {  # 仅本地开发/跑测试用(容器运行不需要)
  command -v uv >/dev/null || { echo "✗ 需要 uv (brew install uv 或 https://astral.sh/uv)" >&2; exit 1; }
  uv sync   # 建 .venv 并按 uv.lock 装依赖 + 本包(editable)
  echo "dev 环境就绪: uv run python -m folia.pipeline.cli ...  /  uv run python -m unittest discover -s tests"
}

cmd_help() {
  cat <<'EOF'
folia.sh — 起停整套 (rsshub + 控制面板)

用法: ./scripts/folia.sh <命令>

  start     构建并拉起 rsshub + 控制面板(http://localhost:8000)
  stop      停止整套(数据在 ./data, 不丢)
  status    容器 + 端口探测
  install   本地 dev 环境(uv sync, 跑测试用)
  help      本帮助

日常操作都在控制面板里: 抓取(启停/间隔)、数据同步、数据源、目录、模型(provider+密钥)、看预览。
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
