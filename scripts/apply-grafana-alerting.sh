#!/usr/bin/env bash
#
# Copy the Git-managed Grafana alerting files to LXC 200 and reload provisioning.
#
# This is intentionally a manual command. It is not connected to Komodo, GitHub
# webhooks, or any automatic sync. Run it after reviewing the diff in Git.
#
# Usage:
#   scripts/apply-grafana-alerting.sh --check
#   scripts/apply-grafana-alerting.sh
#   scripts/apply-grafana-alerting.sh --dashboards
#
# Environment:
#   PVE_HOST   SSH target for the PVE host (default: root@192.168.1.250)
#   LXC_ID     LXC ID running the LGTM stack (default: 200)
#   GRAFANA_URL Local Grafana URL inside the LXC (default: http://127.0.0.1:3000)

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PVE_HOST=${PVE_HOST:-root@192.168.1.250}
LXC_ID=${LXC_ID:-200}
GRAFANA_URL=${GRAFANA_URL:-http://127.0.0.1:3000}

ALERTING_SRC="$ROOT_DIR/pve-lgtm/provisioning/alerting"
ALERTING_DEST=/opt/lgtm/config/grafana/provisioning/alerting
DASHBOARD_SRC="$ROOT_DIR/pve-lgtm/provisioning/dashboards.yaml"
DASHBOARD_DEST=/opt/lgtm/config/grafana/provisioning/dashboards/provider.yaml

CHECK_ONLY=false
SKIP_CHECK=false
APPLY_DASHBOARDS=false
NO_RELOAD=false
RULES_ONLY=false

usage() {
  cat <<'EOF'
Usage: scripts/apply-grafana-alerting.sh [options]

Options:
  --check        Validate the YAML locally and exit.
  --skip-check   Skip local validation (PyYAML may not be installed).
  --rules-only   Copy only 10-*.yaml through 17-*.yaml. Use this for a staged
                 rollout before the Discord webhook is configured; the existing
                 notification policy is left untouched.
  --dashboards   Also copy dashboards.yaml as provider.yaml and reload it.
  --no-reload    Copy files but do not reload Grafana provisioning.
  -h, --help     Show this help.

Environment:
  PVE_HOST    SSH target for the PVE host (default: root@192.168.1.250)
  LXC_ID      LXC ID running the LGTM stack (default: 200)
  GRAFANA_URL Local Grafana URL inside the LXC (default: http://127.0.0.1:3000)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=true ;;
    --skip-check) SKIP_CHECK=true ;;
    --rules-only) RULES_ONLY=true ;;
    --dashboards) APPLY_DASHBOARDS=true ;;
    --no-reload) NO_RELOAD=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [[ "$SKIP_CHECK" == false ]]; then
  python3 "$ROOT_DIR/scripts/check-alerting.py"
fi

if [[ "$CHECK_ONLY" == true ]]; then
  echo "validation only; nothing was copied"
  exit 0
fi

if [[ ! -d "$ALERTING_SRC" ]]; then
  echo "alerting directory not found: $ALERTING_SRC" >&2
  exit 1
fi

echo "checking SSH to $PVE_HOST ..."
if ! ssh -o ConnectTimeout=5 "$PVE_HOST" true; then
  echo "cannot connect to $PVE_HOST" >&2
  exit 1
fi

if [[ "$RULES_ONLY" == false ]]; then
  echo "checking GRAFANA_DISCORD_WEBHOOK_URL in the Grafana container ..."
  if ! ssh "$PVE_HOST" \
    "pct exec $LXC_ID -- bash -lc 'cd /opt/lgtm && docker compose exec -T grafana printenv GRAFANA_DISCORD_WEBHOOK_URL'" \
    >/dev/null 2>&1; then
    cat >&2 <<'EOF'
GRAFANA_DISCORD_WEBHOOK_URL is not set in the Grafana container.

Add the webhook URL to /opt/lgtm/.env and pass it to the Grafana service, then
recreate Grafana:

  # /opt/lgtm/.env (0600)
  GRAFANA_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

  # docker-compose.yml
  services:
    grafana:
      environment:
        GRAFANA_DISCORD_WEBHOOK_URL: ${GRAFANA_DISCORD_WEBHOOK_URL}

  cd /opt/lgtm && docker compose up -d grafana

See pve-lgtm/ALERTING.md for details.
EOF
    exit 1
  fi
fi

echo "copying alerting files to $ALERTING_DEST ..."
ssh "$PVE_HOST" "pct exec $LXC_ID -- mkdir -p $ALERTING_DEST"

if [[ "$RULES_ONLY" == true ]]; then
  echo "  (rules only; routing files are left untouched)"
  ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc 'rm -f $ALERTING_DEST/1[0-9]-*.yaml'"
  files=("$ALERTING_SRC"/1[0-9]-*.yaml)
else
  # Mirror the Git directory: remove YAML files that no longer exist in Git.
  ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc 'rm -f $ALERTING_DEST/*.yaml'"
  files=("$ALERTING_SRC"/*.yaml)
fi

for src in "${files[@]}"; do
  base=$(basename "$src")
  scp -q "$src" "$PVE_HOST:/tmp/$base"
  ssh "$PVE_HOST" "pct push $LXC_ID /tmp/$base $ALERTING_DEST/$base --perms 644"
  ssh "$PVE_HOST" "rm -f /tmp/$base"
  echo "  $base"
done

if [[ "$APPLY_DASHBOARDS" == true ]]; then
  echo "copying dashboard provider ..."
  scp -q "$DASHBOARD_SRC" "$PVE_HOST:/tmp/provider.yaml"
  ssh "$PVE_HOST" "pct push $LXC_ID /tmp/provider.yaml $DASHBOARD_DEST --perms 644"
  ssh "$PVE_HOST" "rm -f /tmp/provider.yaml"
fi

if [[ "$NO_RELOAD" == true ]]; then
  echo "files copied; reload skipped (--no-reload)"
  exit 0
fi

reload() {
  local path=$1
  ssh "$PVE_HOST" \
    "pct exec $LXC_ID -- bash -lc 'PASS=\$(grep -m1 \"^GF_SECURITY_ADMIN_PASSWORD=\" /opt/lgtm/.env | cut -d= -f2-); curl -fsS -u \"admin:\$PASS\" -X POST $GRAFANA_URL$path'"
}

echo "reloading alerting provisioning ..."
if ! reload /api/admin/provisioning/alerting/reload; then
  echo "alerting reload failed; restarting Grafana to apply the files" >&2
  ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc 'cd /opt/lgtm && docker compose restart grafana'"
fi

if [[ "$APPLY_DASHBOARDS" == true ]]; then
  echo "reloading dashboard provisioning ..."
  if ! reload /api/admin/provisioning/dashboards/reload; then
    echo "dashboard reload failed; restarting Grafana" >&2
    ssh "$PVE_HOST" "pct exec $LXC_ID -- bash -lc 'cd /opt/lgtm && docker compose restart grafana'"
  fi
fi

echo "done. Check Alerting -> Alert rules and Contact points in Grafana."
