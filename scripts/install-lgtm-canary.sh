#!/usr/bin/env bash
#
# Install the LGTM canaries on LXC 200.
#
# The scripts run on the LGTM host itself and publish metrics through the local
# OTLP collector.  This command is manual, like apply-grafana-alerting.sh.
#
# Usage:
#   scripts/install-lgtm-canary.sh
#   scripts/install-lgtm-canary.sh --update-baseline
#
# Environment:
#   PVE_HOST   SSH target for the PVE host (default: root@192.168.1.250)
#   LXC_ID     LXC ID running the LGTM stack (default: 200)

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PVE_HOST=${PVE_HOST:-root@192.168.1.250}
LXC_ID=${LXC_ID:-200}
SRC_DIR="$ROOT_DIR/pve-lgtm/canary"
DEST_DIR=/usr/local/lib/lgtm-canary
UNIT_DIR=/etc/systemd/system

UPDATE_BASELINE=false
if [[ "${1:-}" == "--update-baseline" ]]; then
  UPDATE_BASELINE=true
fi

echo "checking SSH to $PVE_HOST ..."
ssh -o ConnectTimeout=5 "$PVE_HOST" true

echo "installing canary scripts ..."
ssh "$PVE_HOST" "pct exec $LXC_ID -- mkdir -p $DEST_DIR /etc/lgtm-canary /var/lib/lgtm-canary"
for file in "$SRC_DIR"/*.py; do
  base=$(basename "$file")
  scp -q "$file" "$PVE_HOST:/tmp/$base"
  ssh "$PVE_HOST" "pct push $LXC_ID /tmp/$base $DEST_DIR/$base --perms 755"
  ssh "$PVE_HOST" "rm -f /tmp/$base"
  echo "  $base"
done

echo "installing systemd units ..."
for unit in \
  lgtm-ingestion-canary.service \
  lgtm-ingestion-canary.timer \
  lgtm-config-canary.service \
  lgtm-config-canary.timer \
  lgtm-gitops-canary.service \
  lgtm-gitops-canary.timer \
  lgtm-r2-usage.service \
  lgtm-r2-usage.timer; do
  scp -q "$SRC_DIR/systemd/$unit" "$PVE_HOST:/tmp/$unit"
  ssh "$PVE_HOST" "pct push $LXC_ID /tmp/$unit $UNIT_DIR/$unit --perms 644"
  ssh "$PVE_HOST" "rm -f /tmp/$unit"
  echo "  $unit"
done

echo "checking required secrets in /opt/lgtm/.env ..."
ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc '
  missing=0
  for key in GF_SECURITY_ADMIN_PASSWORD KOMODO_API_KEY KOMODO_API_SECRET; do
    if ! grep -q \"^\$key=\" /opt/lgtm/.env; then
      echo \"missing: \$key\" >&2
      missing=1
    fi
  done
  if [[ ! -r /root/lgtm-s3.env ]]; then
    echo \"missing: /root/lgtm-s3.env (R2 credentials)\" >&2
    missing=1
  fi
  exit \$missing
'"

echo "creating the Grafana monitor token if needed ..."
ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc '
  if ! grep -q \"^GRAFANA_MONITOR_TOKEN=\" /opt/lgtm/.env; then
    /usr/bin/python3 $DEST_DIR/create_grafana_monitor_token.py
  fi
'"

echo "enabling timers ..."
ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc '
  systemctl daemon-reload
  systemctl enable --now \
    lgtm-ingestion-canary.timer \
    lgtm-config-canary.timer \
    lgtm-gitops-canary.timer \
    lgtm-r2-usage.timer
'"

if [[ "$UPDATE_BASELINE" == true ]]; then
  echo "writing Grafana configuration baseline ..."
  ssh "$PVE_HOST" "pct exec $LXC_ID -- /usr/bin/python3 $DEST_DIR/config_canary.py --update-baseline"
fi

echo "running each canary once ..."
ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc '
  /usr/bin/python3 $DEST_DIR/ingestion_canary.py || true
  /usr/bin/python3 $DEST_DIR/config_canary.py || true
  /usr/bin/python3 $DEST_DIR/gitops_canary.py || true
  /usr/bin/python3 $DEST_DIR/r2_usage.py || true
'"

echo "done. Check:"
echo "  ssh $PVE_HOST 'pct exec $LXC_ID -- systemctl list-timers --no-pager | grep lgtm'"
echo "  Grafana -> Alerting -> Alert rules -> canary / grafana-config / gitops / r2"
