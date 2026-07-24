#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/final_proj/backend"
FRONTEND_DIR="$ROOT_DIR/final_proj/frontend"
DEMO_DIR="$ROOT_DIR/.demo"
VENV_DIR="$DEMO_DIR/venv"

mkdir -p "$DEMO_DIR"

command -v python3 >/dev/null 2>&1 || { echo "Python 3.11+ is required."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "Node.js 20+ is required."; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required."; exit 1; }

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -q -r "$BACKEND_DIR/requirements-demo.txt"

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  (cd "$FRONTEND_DIR" && npm ci)
fi

cleanup() {
  kill "${FRONTEND_PID:-}" "${BACKEND_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd "$BACKEND_DIR" && "$VENV_DIR/bin/python" -m uvicorn demo_main:app --host 127.0.0.1 --port 4311) \
  >"$DEMO_DIR/backend.out.log" 2>"$DEMO_DIR/backend.err.log" &
BACKEND_PID=$!

(cd "$FRONTEND_DIR" && NEXT_PUBLIC_DEMO_MODE=true NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:4311 \
  npm run dev -- --hostname 127.0.0.1 --port 4310) \
  >"$DEMO_DIR/frontend.out.log" 2>"$DEMO_DIR/frontend.err.log" &
FRONTEND_PID=$!

for _ in {1..120}; do
  if curl -fsS http://127.0.0.1:4311/healthz >/dev/null \
    && curl -fsS http://127.0.0.1:4310 >/dev/null; then
    break
  fi
  sleep 1
done

curl -fsS http://127.0.0.1:4311/healthz >/dev/null
curl -fsS http://127.0.0.1:4310 >/dev/null

echo
echo "LocalFit Lab execution demo is ready."
echo "Web: http://127.0.0.1:4310"
echo "API: http://127.0.0.1:4311/docs"
echo "Data: synthetic samples only"

if command -v open >/dev/null 2>&1; then
  open http://127.0.0.1:4310 >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open http://127.0.0.1:4310 >/dev/null 2>&1 || true
fi

wait
