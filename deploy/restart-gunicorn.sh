#!/usr/bin/env bash
# Restart gunicorn after .env or code changes (no sudo). Run on gpu-3 as pyl.
set -euo pipefail
APP=/var/www/stdweb
LOG=~/var/log/stdweb
mkdir -p "$LOG"
cd "$APP"

if command -v fuser >/dev/null 2>&1; then
  fuser -k 8000/tcp 2>/dev/null || true
else
  while read -r pid; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done < <(pgrep -f "${APP}/.venv/bin/gunicorn stdweb.wsgi" || true)
fi
sleep 2

exec "${APP}/.venv/bin/gunicorn" stdweb.wsgi:application \
  --bind 127.0.0.1:8000 \
  --workers 3 \
  --timeout 300 \
  --daemon \
  --capture-output \
  --access-logfile "${LOG}/gunicorn-access.log" \
  --error-logfile "${LOG}/gunicorn-error.log"
