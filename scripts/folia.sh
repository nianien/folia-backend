#!/usr/bin/env bash
# folia.pipeline 一站式脚本: 安装 / 启动 / 入库 / 状态 / 预览。
# 用法: ./scripts/folia.sh <命令> [选项]   (./scripts/folia.sh help 看全部)
set -euo pipefail

# OrbStack 的 docker CLI 不在默认 PATH
export PATH="$HOME/.orbstack/bin:$PATH"
cd "$(dirname "$0")/.."                     # repo 根
export PYTHONPATH="${PYTHONPATH:-src}"

# ---- helpers ----
py() { if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi; }
env_val() { grep "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }   # 安全取值, 不 source
need_docker() { command -v docker >/dev/null 2>&1 || { echo "✗ 找不到 docker(OrbStack 起了吗? open -a OrbStack)" >&2; exit 1; }; }

base_up() {
  need_docker
  echo "==> 拉起基座层(rsshub / fulltextrss / freshrss)"
  docker compose up -d
  local st
  for svc in rsshub fulltextrss freshrss; do
    printf '   %-12s ' "$svc"
    for _ in $(seq 1 60); do
      st=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$svc" '$1==s{print $2}')
      [ "$st" = "running" ] && { echo "running ✓"; break; }
      sleep 2
    done
    [ "${st:-}" = "running" ] || echo "未就绪 ✗ (docker compose logs $svc)"
  done
}

# ---- commands ----
cmd_install() {
  command -v python3 >/dev/null || { echo "✗ 需要 python3" >&2; exit 1; }
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/python -m pip install -U pip
  .venv/bin/python -m pip install -e .          # 含 psycopg(入库要用)
  echo
  echo "下一步:"
  echo "  1) cp .env.example .env  并填 FRESHRSS_* / DATABASE_URL"
  echo "  2) ollama pull bge-m3    (本机 embedding)"
  echo "  3) ./scripts/folia.sh start"
}

cmd_serve() {
  local port=8000
  while [ $# -gt 0 ]; do case "$1" in --port) port="$2"; shift;; *) ;; esac; shift; done
  echo "==> Web UI 预览: http://localhost:$port   (Ctrl-C 退出)"
  exec "$(py)" -m folia.pipeline.cli serve --port "$port"
}

cmd_start() {
  local web=1 port=8000
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-web) web=0;;
      --port) port="$2"; shift;;
      --interval) export PIPELINE_INTERVAL="$2"; shift;;   # 覆盖 pipeline 服务的间隔(秒)
      *) echo "未知参数: $1" >&2; exit 1;;
    esac; shift
  done
  base_up
  echo "   FreshRSS: http://localhost:8080"
  if [ "$web" = 1 ]; then
    echo
    cmd_serve --port "$port"                    # 前台阻塞, Ctrl-C 退出(基座层仍在后台)
  else
    echo "完成。Web UI 未启动(--no-web); 需要时 ./scripts/folia.sh serve"
  fi
}

cmd_stop() { need_docker; docker compose down; echo "已停止(数据在宿主机 ./data, 不丢)。"; }

cmd_status() {
  need_docker
  docker compose ps
  echo
  probe() { curl -s -o /dev/null -m 5 -w "%{http_code}" "$1" 2>/dev/null || echo 000; }
  echo "   FreshRSS   http://localhost:8080  -> $(probe http://localhost:8080)"
  echo "   RSSHub     http://localhost:1200  -> $(probe http://localhost:1200)"
  echo "   Full-Text  http://localhost:8081  -> $(probe http://localhost:8081)"
  echo "   Ollama     http://localhost:11434 -> $(probe http://localhost:11434)"
}

cmd_run() {
  export FRESHRSS_API_URL="$(env_val FRESHRSS_API_URL)"
  export FRESHRSS_USER="$(env_val FRESHRSS_USER)"
  export FRESHRSS_API_PASSWORD="$(env_val FRESHRSS_API_PASSWORD)"
  "$(py)" -m folia.pipeline.cli run-once
}

cmd_publish() {
  local dsn; dsn="$(env_val DATABASE_URL)"
  [ -n "$dsn" ] || { echo "✗ .env 缺 DATABASE_URL" >&2; exit 1; }
  "$(py)" -c "import psycopg" 2>/dev/null || { echo "✗ 当前 python 没装 psycopg; 先跑 ./scripts/folia.sh install" >&2; exit 1; }
  export DATABASE_URL="$dsn"
  "$(py)" -m folia.pipeline.cli export
  "$(py)" -m folia.pipeline.cli load
  echo "完成: 聚合数据已入库 Neon。"
}

cmd_help() {
  cat <<'EOF'
folia.sh — folia.pipeline 一站式脚本

用法: ./scripts/folia.sh <命令> [选项]

  install                       建 .venv 并 pip install -e .(含 psycopg)
  start [--no-web] [--port N] [--interval SEC]
                                拉起全栈(基座层 + pipeline 定时容器) + Web UI 预览(:8000)
                                --no-web 不起 UI; --port 改 UI 端口
                                --interval 改 pipeline 轮询间隔(秒, 默认 .env 或 1800)
  serve [--port N]              只起 Web UI 预览(默认 :8000)
  run                           手动 run-once: 抓取→清洗→聚合(验证用)
  publish                       手动入库: export + load 到 Neon
  stop                          停止整个 compose 栈
  status                        容器 + 端口探测
  help                          本帮助

定时执行: 由 compose 的 `pipeline` 服务负责——start 后它常驻, 每 PIPELINE_INTERVAL
秒(默认 1800)自动跑一轮(run + 入库)。改间隔: .env 里设 PIPELINE_INTERVAL。
首次: install → 填 .env → ollama pull bge-m3 → start
EOF
}

case "${1:-help}" in
  install) shift; cmd_install "$@";;
  start)   shift; cmd_start "$@";;
  serve)   shift; cmd_serve "$@";;
  run)     shift; cmd_run "$@";;
  publish) shift; cmd_publish "$@";;
  stop|down) shift; cmd_stop "$@";;
  status)  shift; cmd_status "$@";;
  help|-h|--help) cmd_help;;
  *) echo "未知命令: ${1:-}" >&2; echo; cmd_help; exit 1;;
esac
