#!/usr/bin/env bash
# 一键拉起基座层(rsshub / fulltextrss / freshrss)并等待就绪。
# 用法:./scripts/base-up.sh
set -euo pipefail

# OrbStack 的 docker CLI 不在默认 PATH
export PATH="$HOME/.orbstack/bin:$PATH"

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "✗ 找不到 docker。确认 OrbStack 已启动(open -a OrbStack)。" >&2
  exit 1
fi

echo "==> 拉取镜像(首次较慢)"
docker compose pull

echo "==> 启动服务"
docker compose up -d

echo "==> 等待容器就绪"
for svc in rsshub fulltextrss freshrss; do
  printf '   %-12s ' "$svc"
  for _ in $(seq 1 60); do
    state=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$svc" '$1==s{print $2}')
    if [ "$state" = "running" ]; then echo "running ✓"; break; fi
    sleep 2
  done
  [ "${state:-}" = "running" ] || { echo "未就绪 ✗(docker compose logs $svc 查看)"; }
done

echo "==> 端口探测"
probe() { curl -s -o /dev/null -m 5 -w "%{http_code}" "$1" 2>/dev/null || echo "000"; }
echo "   FreshRSS   http://localhost:8080  -> $(probe http://localhost:8080)"
echo "   RSSHub     http://localhost:1200  -> $(probe http://localhost:1200)"
echo "   Full-Text  http://localhost:8081  -> $(probe http://localhost:8081)"

echo ""
echo "完成。下一步:浏览器打开 http://localhost:8080 完成 FreshRSS 首次设置"
echo "(建账户 → 开 Google Reader API → 接全文 → 导入 config/freshrss/subscriptions.opml)"
echo "详见 config/freshrss/README.md"
