#!/usr/bin/env bash
#
# Install the external dead-man switch on the ocms-ubuntu VPS.
#
# The monitor runs outside pve2 / LXC 200 and sends Discord notifications
# directly when grafana.usbharu.dev, the OTLP endpoint over WireGuard, Komodo,
# Pocket ID or OCMS is unreachable.
#
# Usage:
#   scripts/install-external-monitor.sh
#
# Environment:
#   VPS_HOST   SSH target for the VPS (default: ubuntu@160.248.91.107)
#   PVE_HOST   SSH target for pve2 (default: root@192.168.1.250)
#   LXC_ID     LGTM LXC (default: 200)

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VPS_HOST=${VPS_HOST:-ubuntu@160.248.91.107}
PVE_HOST=${PVE_HOST:-root@192.168.1.250}
LXC_ID=${LXC_ID:-200}
SRC_DIR="$ROOT_DIR/pve-lgtm/canary"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

echo "reading the Discord webhook from LXC $LXC_ID ..."
WEBHOOK=$(ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc 'grep -m1 \"^GRAFANA_DISCORD_WEBHOOK_URL=\" /opt/lgtm/.env | cut -d= -f2-'")
if [[ -z "$WEBHOOK" ]]; then
  echo "GRAFANA_DISCORD_WEBHOOK_URL is not set in /opt/lgtm/.env" >&2
  exit 1
fi
GRAFANA_TOKEN=$(ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc 'grep -m1 \"^GRAFANA_MONITOR_TOKEN=\" /opt/lgtm/.env | cut -d= -f2-'")
if [[ -z "$GRAFANA_TOKEN" ]]; then
  echo "GRAFANA_MONITOR_TOKEN is not set in /opt/lgtm/.env; Mimir/Loki/Tempo checks will be skipped" >&2
fi

echo "staging files ..."
install -m 755 "$SRC_DIR/otlp.py" "$STAGE/otlp.py"
install -m 755 "$SRC_DIR/external_monitor.py" "$STAGE/external_monitor.py"
install -m 644 "$SRC_DIR/systemd/lab-external-monitor.service" "$STAGE/lab-external-monitor.service"
install -m 644 "$SRC_DIR/systemd/lab-external-monitor.timer" "$STAGE/lab-external-monitor.timer"
umask 077
{
  echo "DISCORD_WEBHOOK_URL=$WEBHOOK"
  echo "OTLP_ENDPOINT=http://192.168.1.240:4318"
  if [[ -n "$GRAFANA_TOKEN" ]]; then
    echo "GRAFANA_MONITOR_TOKEN=$GRAFANA_TOKEN"
  fi
} > "$STAGE/lab-external-monitor.env"

cat > "$STAGE/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
install -d -m 755 /opt/lab-external-monitor /var/lib/lab-external-monitor
install -m 755 otlp.py external_monitor.py /opt/lab-external-monitor/
install -m 600 -o root -g root lab-external-monitor.env /etc/lab-external-monitor.env
install -m 644 lab-external-monitor.service lab-external-monitor.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lab-external-monitor.timer
systemctl start lab-external-monitor.service
journalctl -u lab-external-monitor.service -n 20 --no-pager
EOF
chmod 755 "$STAGE/install.sh"

echo "copying to $VPS_HOST ..."
scp -q -r "$STAGE" "$VPS_HOST:/tmp/lab-external-monitor-install"

echo "installing and starting the timer ..."
ssh "$VPS_HOST" 'sudo -n bash /tmp/lab-external-monitor-install/install.sh; rm -rf /tmp/lab-external-monitor-install'

echo "done. The timer runs every minute and notifies Discord directly on failure."
