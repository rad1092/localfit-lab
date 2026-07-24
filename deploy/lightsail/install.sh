#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/srv/localfit"
PROJECT_ROOT="${APP_ROOT}/final_proj"
BACKEND_ROOT="${PROJECT_ROOT}/backend"
FRONTEND_ROOT="${PROJECT_ROOT}/frontend"
RUNTIME_ROOT="${PROJECT_ROOT}/runtime"
PRIVATE_ROOT="${APP_ROOT}/private"
VENV_ROOT="${PROJECT_ROOT}/.venv"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo."
  exit 1
fi

required_files=(
  "${BACKEND_ROOT}/main.py"
  "${FRONTEND_ROOT}/package-lock.json"
  "${FRONTEND_ROOT}/.env.local"
  "${RUNTIME_ROOT}/db/commercial.db"
  "${PRIVATE_ROOT}/backend.env"
  "${PRIVATE_ROOT}/key.md"
  "${APP_ROOT}/deploy/lightsail/requirements-production.lock.txt"
)
for required_file in "${required_files[@]}"; do
  if [[ ! -f "${required_file}" ]]; then
    echo "Missing required deployment file: ${required_file}"
    exit 1
  fi
done

systemctl stop localfit-frontend.service localfit-backend.service 2>/dev/null || true

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential \
  fontconfig \
  fonts-nanum \
  nginx \
  python3-dev \
  python3-pip \
  python3-venv

install -d -o ubuntu -g ubuntu -m 0755 \
  "${RUNTIME_ROOT}/db/backups" \
  "${RUNTIME_ROOT}/reports" \
  "${RUNTIME_ROOT}/exports" \
  "${RUNTIME_ROOT}/logs" \
  "${RUNTIME_ROOT}/tmp" \
  "${RUNTIME_ROOT}/auth" \
  "${RUNTIME_ROOT}/admin"
install -d -o ubuntu -g ubuntu -m 0700 "${PRIVATE_ROOT}"
chmod 0600 "${PRIVATE_ROOT}/backend.env" "${PRIVATE_ROOT}/key.md"
chown -R ubuntu:ubuntu "${APP_ROOT}"

python3 - <<'PY'
from pathlib import Path

for value in (
    "/srv/localfit/private/backend.env",
    "/srv/localfit/final_proj/frontend/.env.local",
):
    path = Path(value)
    content = path.read_bytes()
    if content.startswith(b"\xef\xbb\xbf"):
        path.write_bytes(content[3:])
PY

python3 -m venv "${VENV_ROOT}"
"${VENV_ROOT}/bin/python" -m pip install --upgrade pip wheel
"${VENV_ROOT}/bin/python" -m pip install \
  -r "${APP_ROOT}/deploy/lightsail/requirements-production.lock.txt"
chown -R ubuntu:ubuntu "${VENV_ROOT}"

runuser -u ubuntu -- env \
  HOME=/home/ubuntu \
  NEXT_PUBLIC_API_BASE_URL=https://whago.net \
  NEXT_TELEMETRY_DISABLED=1 \
  bash -lc "cd '${FRONTEND_ROOT}' && npm ci && npm run build"

runuser -u ubuntu -- env \
  LOCALFIT_ENV=production \
  LOCALFIT_RUNTIME_ROOT="${RUNTIME_ROOT}" \
  LOCALFIT_DATABASE_PATH="${RUNTIME_ROOT}/db/commercial.db" \
  LOCALFIT_DATA_ROOT="${APP_ROOT}/datacorpus" \
  LOCALFIT_BOUNDARY_VERTICES_PATH="${APP_ROOT}/datacorpus/_gold/gold_location_boundary_vertices.csv" \
  LOCALFIT_STORE_POI_PATH="${APP_ROOT}/datacorpus/_silver/silver_sbdc_store_poi_seoul_202603.csv" \
  LOCALFIT_BUS_STOP_PATH="${APP_ROOT}/datacorpus/_silver/silver_bus_stop_location_master.csv" \
  LOCALFIT_SUBWAY_STATION_PATH="${APP_ROOT}/datacorpus/_silver/silver_subway_station_master.csv" \
  "${VENV_ROOT}/bin/python" "${BACKEND_ROOT}/scripts/seed_spatial_index.py"

install -m 0644 \
  "${APP_ROOT}/deploy/lightsail/localfit-backend.service" \
  /etc/systemd/system/localfit-backend.service
install -m 0644 \
  "${APP_ROOT}/deploy/lightsail/localfit-frontend.service" \
  /etc/systemd/system/localfit-frontend.service
install -m 0644 \
  "${APP_ROOT}/deploy/lightsail/nginx-localfit.conf" \
  /etc/nginx/sites-available/localfit
ln -sfn /etc/nginx/sites-available/localfit /etc/nginx/sites-enabled/localfit
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable --now localfit-backend.service
systemctl enable --now localfit-frontend.service
systemctl enable --now nginx.service
systemctl reload nginx.service

echo "LocalFit installation completed."
systemctl --no-pager --full status localfit-backend.service | sed -n '1,8p'
systemctl --no-pager --full status localfit-frontend.service | sed -n '1,8p'
fc-match NanumGothic
