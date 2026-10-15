#!/bin/bash
# Start / restart STDWeb beta gunicorn on gpu-3 (127.0.0.1:8002, DEBUG=True).
# Does not touch production (:8000).
set -euo pipefail

BETA="${BETA:-/var/www/stdweb-beta}"
BIND="${BIND:-127.0.0.1:8002}"
PIDFILE="${PIDFILE:-$HOME/var/run/stdweb-beta-gunicorn.pid}"
LOGDIR="${LOGDIR:-$HOME/var/log/stdweb}"

mkdir -p "$(dirname "$PIDFILE")" "$LOGDIR"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")" || true
    sleep 1
fi
pkill -f 'gunicorn stdweb.wsgi:application --bind 127.0.0.1:8002' 2>/dev/null || true

cd "$BETA"
# shellcheck disable=SC1091
source .venv/bin/activate
nohup gunicorn stdweb.wsgi:application \
    --bind "$BIND" \
    --workers 2 \
    --timeout 300 \
    --pid "$PIDFILE" \
    --access-logfile "$LOGDIR/gunicorn-beta-access.log" \
    --error-logfile "$LOGDIR/gunicorn-beta-error.log" \
    >> "$LOGDIR/gunicorn-beta.log" 2>&1 &

sleep 1
curl -sf -o /dev/null -w "beta_http=%{http_code} ${BIND}\n" "http://${BIND}/login/" \
    || echo "beta_failed — see $LOGDIR/gunicorn-beta-error.log"
echo "LAN test: ssh -L 8002:127.0.0.1:8002 pyl@192.168.1.107  then http://127.0.0.1:8002/"
