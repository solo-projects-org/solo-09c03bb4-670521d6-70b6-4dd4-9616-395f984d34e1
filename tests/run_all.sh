#!/usr/bin/env bash
# 三级自测一键脚本（仅需 Python 3.11 标准库；不依赖 FastAPI/docker）。
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== [1/3] 求解器穷举对照 =="
python3 -I tests/test_solver.py

echo "== [2/3] 服务层端到端 =="
python3 -I tests/test_services.py

echo "== [3/3] HTTP 级（自动启停标准库垫片）=="
DBF="$(mktemp -u /tmp/chrono-selftest.XXXXXX.db)"
PORT="${PORT:-8011}"
CHRONO_DB_PATH="$DBF" PORT="$PORT" python3 -I tests/stdlib_server.py &
SRV_PID=$!
trap 'kill -9 "$SRV_PID" 2>/dev/null || true; rm -f "$DBF" "$DBF-wal" "$DBF-shm"' EXIT

# 等待端口就绪
for _ in $(seq 1 30); do
  if curl -s -m 2 "http://127.0.0.1:$PORT/api/chronology" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

BASE_URL="http://127.0.0.1:$PORT" python3 -I tests/test_http.py
echo
echo "全部自测通过。"
