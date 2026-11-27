#!/usr/bin/env bash
#
# Install the ZFS scrub / vdev error metrics collector on pve3.
#
# Usage:
#   scripts/install-pve3-zfs.sh
#
# Environment:
#   PVE3_HOST   SSH target for pve3 (default: root@192.168.1.251)

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PVE3_HOST=${PVE3_HOST:-root@192.168.1.251}
SRC_DIR="$ROOT_DIR/pve-lgtm/canary"
DEST_DIR=/usr/local/lib/lgtm-canary

echo "checking SSH to $PVE3_HOST ..."
ssh -o ConnectTimeout=5 "$PVE3_HOST" true

echo "installing scripts ..."
ssh "$PVE3_HOST" "mkdir -p $DEST_DIR"
for file in "$SRC_DIR/otlp.py" "$SRC_DIR/zfs_scrub.py"; do
  scp -q "$file" "$PVE3_HOST:/tmp/$(basename "$file")"
  ssh "$PVE3_HOST" "install -m 755 /tmp/$(basename "$file") $DEST_DIR/$(basename "$file") && rm -f /tmp/$(basename "$file")"
done

echo "installing systemd units ..."
for unit in zfs-scrub-metrics.service zfs-scrub-metrics.timer; do
  scp -q "$SRC_DIR/systemd/$unit" "$PVE3_HOST:/tmp/$unit"
  ssh "$PVE3_HOST" "install -m 644 /tmp/$unit /etc/systemd/system/$unit && rm -f /tmp/$unit"
done

echo "enabling the timer ..."
ssh "$PVE3_HOST" 'systemctl daemon-reload && systemctl enable --now zfs-scrub-metrics.timer && systemctl start zfs-scrub-metrics.service'
ssh "$PVE3_HOST" 'journalctl -u zfs-scrub-metrics.service -n 20 --no-pager'

echo "done. Check zfs_pool_scrub_errors and zfs_vdev_errors in Mimir."
