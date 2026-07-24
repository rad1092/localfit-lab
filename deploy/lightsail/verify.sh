#!/usr/bin/env bash
set -euo pipefail

curl -fsS http://127.0.0.1:8000/api/areas/stats >/dev/null
curl -fsS http://127.0.0.1:8000/api/rankings >/dev/null
curl -fsS http://127.0.0.1:8000/api/chatbot/area-options >/dev/null
curl -fsS http://127.0.0.1:8000/api/chatbot/industry-options >/dev/null
curl -fsS http://127.0.0.1:8000/api/spatial/status >/tmp/localfit-spatial-status.json
curl -fsS http://127.0.0.1:3000/ >/dev/null
curl -fsS http://127.0.0.1/healthz >/dev/null

/srv/localfit/final_proj/.venv/bin/python - <<'PY'
import json
from pathlib import Path

status = json.loads(Path("/tmp/localfit-spatial-status.json").read_text())
checks = {
    "boundary_source": status["boundary_source"]["ready"],
    "store_source": status["store_source"]["ready"],
    "bus_stop_source": status["bus_stop_source"]["ready"],
    "subway_source": status["subway_source"]["ready"],
    "store_index": status["store_index_ready"],
    "transit_index": status["transit_index_ready"],
}
failed = [name for name, ready in checks.items() if not ready]
if failed:
    raise SystemExit(f"Spatial checks failed: {', '.join(failed)}")
print("Spatial sources and indexes: OK")
PY

systemctl is-active --quiet localfit-backend.service
systemctl is-active --quiet localfit-frontend.service
systemctl is-active --quiet nginx.service

echo "Backend API: OK"
echo "Frontend: OK"
echo "Nginx: OK"
