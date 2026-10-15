#!/bin/bash
# Rsync this checkout to gpu-3 beta and reload beta gunicorn. Never writes /var/www/stdweb.
set -euo pipefail

ROOT="$(cd "$(dirname "$BASH_SOURCE[0]")/.." && pwd)"
HOST="${HOST:-pyl@192.168.1.107}"
BETA="${BETA:-/var/www/stdweb-beta}"

cd "$ROOT"
rsync -av \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude 'db.sqlite3' \
    --exclude '.env' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'tasks/' \
    --exclude 'data/' \
    --exclude 'blend_radius_for_sergey/' \
    --exclude 'blend_radius_paper/' \
    --exclude 'rapas_multiobs/' \
    --exclude 'stdweb/static/onboarding/src/' \
    ./ "${HOST}:${BETA}/"

ssh "$HOST" "bash ${BETA}/deploy/setup-beta.sh && cd ${BETA} && .venv/bin/python manage.py check && .venv/bin/python manage.py migrate --noinput && .venv/bin/python manage.py collectstatic --noinput && bash ${BETA}/deploy/run-beta.sh"
