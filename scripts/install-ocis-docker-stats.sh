#!/usr/bin/env bash
#
# Add the Docker socket and docker_stats receiver to the oCIS metrics sidecar
# on VM101.  This enables OcisContainerDown / Restarting / Missing.
#
# Usage:
#   scripts/install-ocis-docker-stats.sh
#
# Environment:
#   OCIS_HOST   SSH target for VM101 (default: ubuntu@192.168.1.52)

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OCIS_HOST=${OCIS_HOST:-ubuntu@192.168.1.52}
OCIS_DIR=/home/ubuntu/ocis

echo "checking SSH to $OCIS_HOST ..."
ssh -o ConnectTimeout=5 "$OCIS_HOST" 'sudo -n true'

echo "backing up the current collector configuration ..."
ssh "$OCIS_HOST" "cp -a $OCIS_DIR/ocis-metrics-collector.yaml $OCIS_DIR/ocis-metrics-collector.yaml.bak-\$(date +%Y%m%dT%H%M%S)"
ssh "$OCIS_HOST" "cp -a $OCIS_DIR/compose.override.yaml $OCIS_DIR/compose.override.yaml.bak-\$(date +%Y%m%dT%H%M%S)"

echo "copying the collector configuration ..."
scp -q "$ROOT_DIR/pve-lgtm/examples/ocis-metrics-collector.yaml" "$OCIS_HOST:/tmp/ocis-metrics-collector.yaml"
ssh "$OCIS_HOST" "install -m 644 /tmp/ocis-metrics-collector.yaml $OCIS_DIR/ocis-metrics-collector.yaml && rm -f /tmp/ocis-metrics-collector.yaml"

echo "adding the Docker socket mount ..."
DOCKER_GID=$(ssh "$OCIS_HOST" 'stat -c %g /var/run/docker.sock')
ssh "$OCIS_HOST" "grep -q '/var/run/docker.sock' $OCIS_DIR/compose.override.yaml || \
  sed -i '/ocis-metrics-collector.yaml/a\\      - /var/run/docker.sock:/var/run/docker.sock:ro' $OCIS_DIR/compose.override.yaml"
ssh "$OCIS_HOST" "grep -q 'group_add:' $OCIS_DIR/compose.override.yaml || \
  sed -i '/container_name: ocis-metrics/a\\    group_add:\\n      - \"$DOCKER_GID\"' $OCIS_DIR/compose.override.yaml"

echo "recreating the ocis-metrics container ..."
ssh "$OCIS_HOST" 'cd /home/ubuntu/ocis && sudo -n docker compose up -d ocis-metrics && sudo -n docker compose restart ocis-metrics'
sleep 5
ssh "$OCIS_HOST" 'sudo -n docker logs --since 2m ocis-metrics 2>&1 | tail -20'

echo "done. Wait one scrape interval, then check container_state_status and container_restarts in Mimir."
