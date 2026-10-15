#!/usr/bin/env bash
# Celery worker for production (same DB and .env as gunicorn on stdweb.org.uk).
set -euo pipefail
APP=/var/www/stdweb
LOG="${HOME}/var/log/stdweb"
mkdir -p "$LOG"
cd "$APP"

# Stop ANY stdweb Celery (including stray ~/repo/stdweb workers that cause DoesNotExist).
pkill -f "celery -A stdweb worker" 2>/dev/null || true
sleep 2

exec nohup "${APP}/.venv/bin/celery" -A stdweb worker \
  --concurrency=4 \
  --max-tasks-per-child=20 \
  --loglevel=INFO \
  >> "${LOG}/celery.log" 2>&1 &
