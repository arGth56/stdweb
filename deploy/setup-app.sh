#!/bin/bash
# Run on gpu-3 as user pyl: bash /var/www/stdweb/deploy/setup-app.sh
set -euo pipefail

APP_ROOT="${APP_ROOT:-/var/www/stdweb}"
STPIPE_ROOT="${STPIPE_ROOT:-/home/pyl/repo/stdpipe}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON="$("$SCRIPT_DIR/find-python.sh")"
echo "Using Python: $PYTHON ($("$PYTHON" --version))"

cd "$APP_ROOT"

if [[ ! -d .venv ]]; then
    "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
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

mkdir -p /var/lib/stdweb/data /var/lib/stdweb/tasks /var/lib/stdweb/static
grep -q '^DATA_PATH=' .env && sed -i 's|^DATA_PATH=.*|DATA_PATH=/var/lib/stdweb/data|' .env || echo 'DATA_PATH=/var/lib/stdweb/data' >> .env
grep -q '^TASKS_PATH=' .env && sed -i 's|^TASKS_PATH=.*|TASKS_PATH=/var/lib/stdweb/tasks|' .env || echo 'TASKS_PATH=/var/lib/stdweb/tasks' >> .env
grep -q '^STATIC_ROOT=' .env && sed -i 's|^STATIC_ROOT=.*|STATIC_ROOT=/var/lib/stdweb/static|' .env || echo 'STATIC_ROOT=/var/lib/stdweb/static' >> .env

python manage.py migrate --noinput
python manage.py collectstatic --noinput

echo "OK — sudo systemctl restart stdweb-gunicorn stdweb-celery nginx"
