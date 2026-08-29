#!/bin/bash
# Throttled rsync of STDWeb task archive to production (gpu-3 @ 192.168.1.107).
#
# Usage:
#   BWLIMIT_KB=2000 bash deploy/rsync-tasks-to-gpu3.sh          # foreground
#   BWLIMIT_KB=2000 nohup bash deploy/rsync-tasks-to-gpu3.sh &  # background
#
# BWLIMIT_KB: rsync --bwlimit (kB/s). Default 2000 ≈ 2 MB/s.

set -euo pipefail

SRC="${SRC:-/media/pyl/Expansion/tasks/}"
DEST="${DEST:-pyl@192.168.1.107:/var/lib/stdweb/tasks/}"
BWLIMIT_KB="${BWLIMIT_KB:-2000}"
LOG="${LOG:-$HOME/var/log/stdweb-tasks-rsync.log}"

mkdir -p "$(dirname "$LOG")"

if pgrep -x rsync >/dev/null; then
    echo "Another rsync is already running (pid $(pgrep -x rsync)). Stop it first."
    exit 1
fi

echo "Starting throttled rsync: $SRC -> $DEST"
echo "  --bwlimit=${BWLIMIT_KB} kB/s (~$(( BWLIMIT_KB * 3600 / 1024 / 1024 )) GB/hour max)"
echo "  log: $LOG"

exec rsync -aH --partial --info=progress2 --bwlimit="$BWLIMIT_KB" \
    "$SRC" "$DEST" 2>&1 | tee -a "$LOG"
