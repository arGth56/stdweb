#!/bin/bash
# Mount the 12.7 TB data disk on gpu-3 for STDWeb task storage.
#
# Run ON gpu-3 with sudo:
#   sudo bash ~/repo/stdweb/deploy/gpu3-mount-data-disk.sh
#
# Options (env vars):
#   DISK=/dev/sda2          partition to use (default)
#   MOUNT=/var/lib/stdweb     mount point (TASKS_PATH will be $MOUNT/tasks)
#   FORMAT=0                  set FORMAT=1 to create ext4 (DESTROYS partition data)
#   MIGRATE_FROM=/var/lib/stdweb/tasks   rsync existing tasks after mount (optional)
#
# Why ext4, not exFAT:
#   STDWeb creates one directory per task with many small files; exFAT lacks
#   Unix ownership, symlinks, and performs poorly for this workload.

set -euo pipefail

DISK="${DISK:-/dev/sda2}"
MOUNT="${MOUNT:-/var/lib/stdweb}"
FORMAT="${FORMAT:-0}"
MIGRATE_FROM="${MIGRATE_FROM:-}"
APP_ENV="${APP_ENV:-/var/www/stdweb/.env}"
OWNER="${OWNER:-pyl:pyl}"
FSTAB_OPTS="defaults,noatime,nofail"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0"
    exit 1
fi

if [[ ! -b "$DISK" ]]; then
    echo "Block device not found: $DISK"
    lsblk
    exit 1
fi

echo "=== Disk ==="
lsblk "$DISK" || lsblk
file -s "$DISK" || true

if [[ "$FORMAT" == "1" ]]; then
    echo "WARNING: formatting $DISK as ext4 (all data on partition will be lost)"
    read -r -p "Type YES to continue: " confirm
    [[ "$confirm" == "YES" ]] || exit 1
    umount "$DISK" 2>/dev/null || true
    mkfs.ext4 -L stdweb-data -F "$DISK"
fi

mkdir -p "$MOUNT"
FS_TYPE=$(blkid -o value -s TYPE "$DISK" 2>/dev/null || true)
if [[ "$FS_TYPE" != "ext4" && "$FS_TYPE" != "exfat" && "$FS_TYPE" != "vfat" ]]; then
    echo "No supported filesystem on $DISK (type=$FS_TYPE)."
    echo "Set FORMAT=1 to create ext4, or mount manually."
    exit 1
fi

if [[ "$FS_TYPE" == "exfat" ]]; then
    echo "NOTE: exFAT detected. Works for a quick test; use FORMAT=1 ext4 for production."
    apt-get install -y exfatprogs 2>/dev/null || true
fi

if ! mountpoint -q "$MOUNT"; then
    mount "$DISK" "$MOUNT"
fi

mkdir -p "$MOUNT"/{tasks,data,db}
chown -R "$OWNER" "$MOUNT"/{tasks,data,db}

UUID=$(blkid -o value -s UUID "$DISK")
FSTAB_LINE="UUID=$UUID  $MOUNT  $FS_TYPE  $FSTAB_OPTS  0  2"
if ! grep -q "$UUID" /etc/fstab 2>/dev/null; then
    cp /etc/fstab /etc/fstab.bak.$(date +%Y%m%d%H%M)
    echo "$FSTAB_LINE" >> /etc/fstab
    echo "Added fstab entry: $FSTAB_LINE"
fi

if [[ -n "$MIGRATE_FROM" && -d "$MIGRATE_FROM" ]]; then
    echo "Migrating tasks from $MIGRATE_FROM ..."
    rsync -aH --info=progress2 "$MIGRATE_FROM/" "$MOUNT/tasks/"
    chown -R "$OWNER" "$MOUNT/tasks"
fi

if [[ -f "$APP_ENV" ]]; then
    sed -i "s|^TASKS_PATH=.*|TASKS_PATH=$MOUNT/tasks|" "$APP_ENV"
    sed -i "s|^DATA_PATH=.*|DATA_PATH=$MOUNT/data|" "$APP_ENV"
    echo "Updated $APP_ENV"
    grep -E '^TASKS_PATH|^DATA_PATH' "$APP_ENV"
fi

echo ""
echo "=== Done ==="
df -h "$MOUNT"
echo ""
echo "Restart STDWeb (adjust if you use systemd user units):"
echo "  sudo systemctl restart stdweb-gunicorn stdweb-celery 2>/dev/null || true"
echo "  pkill -HUP -f 'gunicorn stdweb.wsgi' 2>/dev/null || true"
echo ""
echo "To copy the full archive from STD (~2 TiB on Expansion):"
echo "  rsync -aH --info=progress2 pyl@<STD_HOST>:/media/pyl/Expansion/tasks/ $MOUNT/tasks/"
