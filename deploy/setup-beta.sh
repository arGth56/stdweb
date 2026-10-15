#!/bin/bash
# One-time setup of the gpu-3 beta instance (no sudo).
# Code lives in /var/www/stdweb-beta; venv, DB and tasks are shared with prod.
set -euo pipefail

PROD="${PROD:-/var/www/stdweb}"
BETA="${BETA:-/var/www/stdweb-beta}"
STATIC="${STATIC:-/var/lib/stdweb-beta/static}"
LOGDIR="${LOGDIR:-$HOME/var/log/stdweb}"

mkdir -p "$BETA" "$STATIC" "$LOGDIR" "$HOME/var/run"

if [[ ! -d "$PROD/.venv" ]]; then
    echo "Expected prod venv at $PROD/.venv" >&2
    exit 1
fi

ln -sfn "$PROD/.venv" "$BETA/.venv"

if [[ ! -e "$BETA/db.sqlite3" ]]; then
    ln -sfn "$PROD/db.sqlite3" "$BETA/db.sqlite3"
fi

if [[ ! -f "$BETA/.env" ]]; then
    if [[ -f "$PROD/.env" ]]; then
        cp "$PROD/.env" "$BETA/.env"
    else
        echo "No $PROD/.env to copy" >&2
        exit 1
    fi
fi

python3 - <<PY
from pathlib import Path
p = Path("$BETA/.env")
text = p.read_text()
def set_key(text, key, value):
    lines = []
    found = False
    for line in text.splitlines():
        if line.startswith(key + "=") or line.startswith(key + " ="):
            lines.append(f"{key}={value}")
            found = True
        else:
            lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"
text = set_key(text, "DEBUG", "True")
text = set_key(text, "STATIC_ROOT", "$STATIC")
p.write_text(text)
print("beta .env: DEBUG=True STATIC_ROOT=$STATIC")
PY

chmod 600 "$BETA/.env"
echo "OK — rsync code with deploy/rsync-to-beta.sh then start with deploy/run-beta.sh"
