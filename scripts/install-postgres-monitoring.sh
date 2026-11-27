#!/usr/bin/env bash
#
# Install postgres_exporter, the verified daily backup and their systemd units
# on db-pve3 (VM 121).
#
# Usage:
#   scripts/install-postgres-monitoring.sh
#
# Environment:
#   DB_HOST   SSH target for db-pve3 (default: ubuntu@192.168.1.253)

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DB_HOST=${DB_HOST:-ubuntu@192.168.1.253}
SRC_DIR="$ROOT_DIR/pve-lgtm/canary"
DEST_DIR=/usr/local/lib/lgtm-canary
ENV_DIR=/etc/lgtm-canary
EXPORTER_ENV="$ENV_DIR/pg-exporter.env"

echo "checking SSH to $DB_HOST ..."
ssh -o ConnectTimeout=5 "$DB_HOST" 'sudo -n true'

echo "installing prometheus-postgres-exporter ..."
ssh "$DB_HOST" 'sudo -n apt-get update -qq && sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -qq prometheus-postgres-exporter'

echo "preparing the monitoring role ..."
ssh "$DB_HOST" "sudo -n bash -lc '
  mkdir -p $ENV_DIR
  if [[ ! -s $EXPORTER_ENV ]]; then
    password=\$(openssl rand -hex 24)
    umask 077
    printf \"PG_EXPORTER_PASSWORD=%s\\n\" \"\$password\" > $EXPORTER_ENV
  fi
  . $EXPORTER_ENV
  if ! sudo -u postgres psql -p 5434 -d postgres -tAc \"SELECT 1 FROM pg_roles WHERE rolname = '\''postgres_exporter'\''\" | grep -q 1; then
    sudo -u postgres psql -p 5434 -d postgres -c \"CREATE ROLE postgres_exporter LOGIN PASSWORD '\''\$PG_EXPORTER_PASSWORD'\''\"
  else
    sudo -u postgres psql -p 5434 -d postgres -c \"ALTER ROLE postgres_exporter WITH LOGIN PASSWORD '\''\$PG_EXPORTER_PASSWORD'\''\"
  fi
  sudo -u postgres psql -p 5434 -d postgres -c \"GRANT pg_monitor TO postgres_exporter\"
'"

echo "configuring the exporter ..."
ssh "$DB_HOST" "sudo -n bash -lc '
  . $EXPORTER_ENV
  umask 077
  cat > /etc/default/prometheus-postgres-exporter <<EOF
DATA_SOURCE_NAME=\"postgresql://postgres_exporter:\$PG_EXPORTER_PASSWORD@127.0.0.1:5434/ocms?sslmode=disable\"
EOF
  chmod 600 /etc/default/prometheus-postgres-exporter
  systemctl enable --now prometheus-postgres-exporter
  systemctl restart prometheus-postgres-exporter
'"

echo "installing backup scripts and units ..."
ssh "$DB_HOST" "sudo -n mkdir -p $DEST_DIR"
for file in "$SRC_DIR/otlp.py" "$SRC_DIR/postgres_backup.py"; do
  scp -q "$file" "$DB_HOST:/tmp/$(basename "$file")"
  ssh "$DB_HOST" "sudo -n install -m 755 /tmp/$(basename "$file") $DEST_DIR/$(basename "$file") && rm -f /tmp/$(basename "$file")"
done
for unit in postgres-backup.service postgres-backup.timer; do
  scp -q "$SRC_DIR/systemd/$unit" "$DB_HOST:/tmp/$unit"
  ssh "$DB_HOST" "sudo -n install -m 644 /tmp/$unit /etc/systemd/system/$unit && rm -f /tmp/$unit"
done

echo "running the first verified backup ..."
ssh "$DB_HOST" 'sudo -n systemctl daemon-reload && sudo -n systemctl enable --now postgres-backup.timer && sudo -n systemctl start postgres-backup.service'

echo "checking exporter and backup metrics ..."
ssh "$DB_HOST" 'curl -fsS http://127.0.0.1:9187/metrics | grep -E "^pg_up|^pg_settings_max_connections|^pg_stat_database_numbackends" | head'
ssh "$DB_HOST" 'sudo -n journalctl -u postgres-backup.service -n 20 --no-pager'

echo "done. Add 192.168.1.253:9187 to the central collector scrape config."
