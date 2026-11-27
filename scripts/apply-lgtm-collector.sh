#!/usr/bin/env bash
#
# Copy the central OTel Collector configuration to LXC 200 and recreate the
# collector.  Run this after changing pve-lgtm/examples/central-collector.yaml.
#
# Usage:
#   scripts/apply-lgtm-collector.sh
#
# Environment:
#   PVE_HOST   SSH target for pve2 (default: root@192.168.1.250)
#   LXC_ID     LGTM LXC (default: 200)

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PVE_HOST=${PVE_HOST:-root@192.168.1.250}
LXC_ID=${LXC_ID:-200}
SRC="$ROOT_DIR/pve-lgtm/examples/central-collector.yaml"

echo "checking SSH to $PVE_HOST ..."
ssh -o ConnectTimeout=5 "$PVE_HOST" true

stamp=$(date +%Y%m%dT%H%M%S)
echo "backing up the live collector configuration ..."
ssh "$PVE_HOST" "pct exec $LXC_ID -- cp -a /opt/lgtm/config/otel-collector.yaml /opt/lgtm/config/otel-collector.yaml.bak-$stamp"

echo "copying the new configuration ..."
scp -q "$SRC" "$PVE_HOST:/tmp/otel-collector.yaml"
ssh "$PVE_HOST" "pct push $LXC_ID /tmp/otel-collector.yaml /opt/lgtm/config/otel-collector.yaml --perms 644"
ssh "$PVE_HOST" "rm -f /tmp/otel-collector.yaml"

echo "recreating the collector ..."
if ! ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc 'cd /opt/lgtm && docker compose up -d otel-collector && docker compose restart otel-collector'"; then
  echo "collector failed to start; restoring the previous configuration" >&2
  ssh "$PVE_HOST" "pct exec $LXC_ID -- cp -a /opt/lgtm/config/otel-collector.yaml.bak-$stamp /opt/lgtm/config/otel-collector.yaml && cd /opt/lgtm && docker compose restart otel-collector"
  exit 1
fi

sleep 5
ssh "$PVE_HOST" "pct exec $LXC_ID -- docker logs --since 2m lgtm-otel-collector-1 2>&1 | tail -30"
echo "done. Check the new prometheus/postgres target in Mimir."
