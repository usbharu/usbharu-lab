# Ubuntu / Docker / OCMS observability

Grafana Alloy v1.21.2 collects this Ubuntu host, Docker containers, Komodo logs and OCMS telemetry, and sends metrics, logs, and traces to the local PVE LGTM stack over a WireGuard link. Grafana Cloud is no longer used. Komodo deploys the image and configuration from the `komodo` branch; no manual `docker pull` is needed on the server.

## Signals

| Source | Collected data | Destination |
| --- | --- | --- |
| Ubuntu | CPU, load, pressure, memory, swap, filesystems, disk I/O and network, every 30 seconds | PVE LGTM (Mimir) |
| Docker containers | CPU, throttling, and memory working set, including Komodo and OCMS | PVE LGTM (Mimir) |
| Komodo | Core, Periphery and MongoDB stdout/stderr, collected through Docker | PVE LGTM (Loki) |
| Docker daemon | `docker.service` journal; only entries from the last hour on first startup | PVE LGTM (Loki) |
| OCMS | Native OTLP/HTTP metrics, logs and traces from Go services, Runtime and recommender | PVE LGTM (Mimir / Loki / Tempo) |
| Alloy | Scrape health and OTLP exporter delivery and failure counters, every 30 seconds | PVE LGTM (Mimir) |

OCMS stdout is deliberately excluded from Docker log collection to avoid duplicating its OTLP logs. Pocket ID, Traefik, other application logs and system journals other than Docker are excluded. Docker metrics do include all containers. Environment variable values and arbitrary container labels are not collected; only Compose project/service labels are retained. No host file contents or private media are scraped.

Infrastructure scrapes forward the metric families used by the dashboards, scrape health, and OTLP exporter delivery counters. This applies to Ubuntu, cAdvisor and Alloy self-metrics. OCMS application metrics, logs and traces use the same PVE LGTM path.

Alloy drops successful HTTP 204 POST spans from the Worker client when the destination is Catalog, and the corresponding class of Catalog server spans. The application does not export the request path, so this filter also covers other Catalog 204 POSTs such as event acknowledgments. Error spans and other HTTP statuses still pass through.

`ocms-telemetry` is an internal Docker network. OTLP port 4318 is not published on the host. Alloy's health/metrics UI is available only through `127.0.0.1:12345`; use an SSH tunnel for remote access. Alloy no longer needs external egress for telemetry; metrics, logs and traces use the host's WireGuard route to PVE LGTM.

cAdvisor requires privileged access, the Docker socket and host filesystem mounts, following the official Alloy Docker deployment. This is a dedicated monitoring container. Limits are CPU 0.5, memory 512 MiB (swap-inclusive 768 MiB); the OTLP memory limiter is 320 MiB and queues are bounded. Export retries for up to five minutes, after which a failing batch may be lost; the sending queue is in memory, not a durable archive. Docker log offsets are stored in `alloy-data`. This is monitoring, not a log backup.

## Telemetry destination

All signals are exported over OTLP/HTTP to `http://192.168.1.240:4318`, the central OpenTelemetry Collector in LXC 200 on pve2. That collector writes metrics to Mimir, logs to Loki, and traces to Tempo. Grafana at https://grafana.usbharu.dev queries them through the `mimir`, `loki`, and `tempo` datasources. The same collector also forwards all three signals to New Relic; see `pve-lgtm/README.md` for the license-key setup.

The path is a WireGuard tunnel between this host and pve2:

- ocms-ubuntu `wg0`: `10.0.0.1/24` (existing VPN hub)
- pve2 `wg0`: `10.0.0.3/32`
- On ocms-ubuntu, `192.168.1.240/32` routes through `wg0`.
- On pve2, IPv4 forwarding is enabled and `10.0.0.0/24` is masqueraded to `vmbr0`.

Example configuration is in `pve-lgtm/examples/wireguard-pve2.conf` and `pve-lgtm/examples/wireguard-vps-peer.conf`. The pve-lgtm collector is reachable only on the LAN/VPN and has no authentication; do not expose port 4318 to the internet.

If the tunnel is down, Alloy retries for five minutes and then drops the batch. Check `wg show` on both sides, then `nc -vz 192.168.1.240 4318` from the host and from inside the Alloy container.

## Host credentials

No Grafana Cloud credentials are needed. The Alloy Compose service only sets `GOMEMLIMIT`; there is no ingestion token or endpoint in the environment. After Komodo has applied the updated stack definition, remove `/etc/komodo/secrets/observability/cloud.env` if it still exists. Authentication for Grafana MCP is separate from Alloy ingestion.

## Deployment

The Resource Sync updates definitions without deploying stacks. The `usbharu-lab-gitops` procedure then deploys observability, OCMS, and management in that order. This creates the shared telemetry network before OCMS joins it. For the first installation, run the Resource Sync first, then start a fresh procedure so that it uses the updated stage order.

The OCMS configuration points each instrumented service to `http://alloy:4318` and marks the environment as `production`. There is no DB migration, application image update or model download. Compose recreates the affected application containers to apply the environment/network changes. Models, DB, R2 objects and crypto keys are preserved.

Alloy configuration changes are tracked through Komodo's `config_files` and restart only Alloy. Follow the procedure result and then check:

```sh
curl -f http://127.0.0.1:12345/-/ready
curl -fsS http://127.0.0.1:12345/metrics
```

Check exporter success/failure counters and query recent metrics, logs and traces in PVE LGTM. A healthy receiver alone does not prove successful export. The dashboards intentionally show no data for missing/idle signals rather than claiming zero failures.

From pve2, verify the local metrics path:

```sh
pct exec 200 -- docker exec lgtm-mimir-1 wget -qO- \
  'http://127.0.0.1:9009/prometheus/api/v1/query?query=up'
pct exec 200 -- docker logs --since 10m lgtm-otel-collector-1
```

On ocms-ubuntu, check the Alloy exporter counters and the tunnel:

```sh
curl -fsS http://127.0.0.1:12345/metrics | grep 'otelcol_exporter_.*pve_lgtm'
wg show
nc -vz 192.168.1.240 4318
```

## Dashboards

The two dashboard JSON files use datasource variables for Prometheus, Loki and Tempo:

- `usbharulab-infra.json`: Ubuntu saturation, container CPU/memory/throttling, Komodo load, Docker journal/Komodo logs, Alloy exporter failures.
- `usbharulab-ocms.json`: HTTP throughput/5xx/p95, completed/failed jobs, processing duration, transfers, container CPU, logs and slow traces.

`usbharulab-ocms.json` has a `Worker` variable to switch the log panel between `All`, `VPS (ocms-ubuntu)` and `VM110 (ocms-worker-vm110)`. Both worker logs are now in the pve-lgtm Loki.

Metrics use `lab_host="ocms-ubuntu"` for the VPS worker and `lab_host="ocms-worker-vm110"` for the PVE worker. Media job panels include both. Metrics select OCMS services with the Prometheus `job` label (`usbharu-lab/<service>`); logs and traces use the resource namespace `usbharu-lab` and the `service_name` label. OCMS does not expose a queue-length gauge, so these dashboards do not invent one. The job counter records completed attempts and final failures/retries according to the app, and is not an exact DB backlog count. Runtime operations and Go HTTP handling expose different instrumentation; HTTP panels use the Go histogram. Existing application code reports a fixed `service.version`, so use container images to identify deployed releases.

The same JSON files are provisioned into the PVE LGTM Grafana. There, select the `mimir`, `loki`, and `tempo` datasources. The files are copied to LXC 200 as described in `pve-lgtm/README.md`; there is no separate Cloud copy.

## Validation

Validate Compose and run `alloy validate` using the pinned image before deployment. Local smoke tests can send generated OTLP data to an isolated receiver; do not inject fixtures into production. Dashboard queries and production resource usage are verified after deployment.

Official references:

- https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.exporter.cadvisor/
- https://grafana.com/docs/alloy/latest/reference/components/otelcol/otelcol.exporter.otlphttp/

Validation performed on 2026-10-28: the pinned Alloy image accepted `alloy validate`; the isolated Linux Docker smoke test reported healthy components, collected Ubuntu and Docker metrics with the expected host/Compose labels, received generated metrics/logs/traces over OTLP/HTTP, and exported all three signals to a mock OTLP endpoint. A generated Komodo container log was exported with `service.name=komodo-core`. The shell readiness probe returned success. Smoke-test memory was approximately 265 MiB, which is a single local observation rather than a production bound.

Validation performed on 2026-11-11: the WireGuard tunnel between ocms-ubuntu (`10.0.0.1`) and pve2 (`10.0.0.3`) came up, and TCP `192.168.1.240:4318` succeeded from the host and from inside the Alloy container. After Komodo restarted Alloy, Mimir returned `up{instance="ocms-ubuntu"}=1` plus Ubuntu, Docker and OCMS series. Queries for `http_server_request_duration_count`, `media_job_completed`, `media_transcode_duration_*`, `media_(upload|download|crypto)_bytes`, `container_cpu_usage_seconds_total` and `node_cpu_seconds_total` returned data. Logs and traces were still on Grafana Cloud in that revision.

Validation performed on 2026-11-19: Grafana Cloud was removed and the pinned Alloy v1.21.2 image accepted `alloy validate`. After Komodo deployed the stack, the pve-lgtm Loki contained Komodo logs with `host_name="ocms-ubuntu"`, and Tempo returned worker traces with `resource.host.name="ocms-ubuntu"`. Mimir continued to receive metrics. The central collector's sent counters for Mimir, Loki, Tempo and New Relic matched, with no send failures.

Regression checks: `python3 scripts/check-config.py` and `git diff --check` passed. The system `python3` may lack `tomllib`; configuration validation requires Python 3.11 or later.
