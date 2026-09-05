#!/bin/bash
# User-level bootstrap on gpu-3 when sudo is not available yet.
set -euo pipefail

APP_ROOT="${APP_ROOT:-$HOME/repo/stdweb}"
STPIPE_ROOT="${STPIPE_ROOT:-$HOME/repo/stdpipe}"
CONDA="$HOME/miniconda3"

if [[ ! -x "$CONDA/bin/redis-server" ]]; then
    if [[ ! -x "$CONDA/bin/conda" ]]; then
        curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
        bash /tmp/miniconda.sh -b -p "$CONDA"
    fi
    "$CONDA/bin/conda" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main 2>/dev/null || true
    "$CONDA/bin/conda" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r 2>/dev/null || true
    "$CONDA/bin/conda" install -y -c conda-forge redis python=3.12
fi

export PATH="$CONDA/bin:$PATH"

cd "$APP_ROOT"
if [[ ! -d .venv ]]; then
    python -m venv .venv
fi
source .venv/bin/activate
pip install -U pip wheel
pip install "setuptools<81"
pip install -r requirements.txt gunicorn
pip install -e "$STPIPE_ROOT"

if [[ ! -f .env ]]; then
    SECRET=$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')
    sed "s/REPLACE_ME/$SECRET/" deploy/gpu3.env.example > .env
    chmod 600 .env
fi

mkdir -p "$HOME/var/lib/stdweb/data" "$HOME/var/lib/stdweb/tasks" "$HOME/var/lib/stdweb/static"
grep -q '^DATA_PATH=' .env || true
sed -i "s|^DATA_PATH=.*|DATA_PATH=$HOME/var/lib/stdweb/data|" .env
sed -i "s|^TASKS_PATH=.*|TASKS_PATH=$HOME/var/lib/stdweb/tasks|" .env
sed -i "s|^STATIC_ROOT=.*|STATIC_ROOT=$HOME/var/lib/stdweb/static|" .env

if ! redis-cli ping >/dev/null 2>&1; then
    redis-server --daemonize yes --dir "$HOME/var/lib/stdweb" --logfile "$HOME/var/lib/stdweb/redis.log"
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput

mkdir -p "$HOME/var/log/stdweb"
pkill -f 'gunicorn stdweb.wsgi' 2>/dev/null || true
pkill -f 'celery -A stdweb worker' 2>/dev/null || true

nohup .venv/bin/gunicorn stdweb.wsgi:application \
    --bind 127.0.0.1:8000 --workers 3 --timeout 300 \
    >> "$HOME/var/log/stdweb/gunicorn.log" 2>&1 &

nohup .venv/bin/celery -A stdweb worker --concurrency=4 --loglevel=INFO \
    >> "$HOME/var/log/stdweb/celery.log" 2>&1 &

sleep 2
curl -sf -o /dev/null -w 'gunicorn_http=%{http_code}\n' http://127.0.0.1:8000/ || echo gunicorn_failed

echo ""
echo "App on http://127.0.0.1:8000 — Cloudflare tunnel must point to http://localhost:8000"
echo "OR run: sudo bash $APP_ROOT/deploy/install-system.sh  (nginx on :80)"
