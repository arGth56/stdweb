#!/usr/bin/env bash
# Grow / to use free space in the LVM volume group (gpu-3 NVMe ~500 GB, root LV often 100 GB).
# Run on gpu-3: sudo bash /var/www/stdweb/deploy/expand-root-lv-gpu3.sh
set -euo pipefail

LV=/dev/ubuntu-vg/ubuntu-lv
MP=/dev/mapper/ubuntu--vg-ubuntu--lv

echo "Before:"
df -h /
vgs ubuntu-vg 2>/dev/null || vgs
lvs ubuntu-vg/ubuntu-lv 2>/dev/null || lvs

echo ""
echo "Extending logical volume to all free space in ubuntu-vg..."
lvextend -l +100%FREE "$LV"

echo ""
echo "Growing ext4 filesystem..."
resize2fs "$MP"

echo ""
echo "After:"
df -h /
vgs ubuntu-vg
