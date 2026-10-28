# Ubuntu / Docker / OCMS observability

Grafana Alloy v1.21.2 collects this Ubuntu host and sends metrics, logs and traces to the Grafana Cloud OTLP gateway. Komodo deploys the image and configuration from the `komodo` branch; no manual `docker pull` is needed on the server.

## Signals

| Source | Collected data |
| --- | --- |
| Ubuntu | CPU, load, pressure, memory, swap, filesystems, disk I/O and network, every 30 seconds |
| Docker containers | CPU, throttling, memory, network, disk I/O and process metrics, including Komodo and OCMS |
| Komodo | Core, Periphery and MongoDB stdout/stderr, collected through Docker |
| Docker daemon | `docker.service` journal; only entries from the last hour on first startup |
| OCMS | Native OTLP/HTTP metrics, logs and traces from Go services, Runtime and recommender |
| Alloy | Collector health and exporter metrics, every 30 seconds |

OCMS stdout is deliberately excluded from Docker log collection to avoid duplicating its OTLP logs. Pocket ID, Traefik, other application logs and system journals other than Docker are excluded. Docker metrics do include all containers. Environment variable values and arbitrary container labels are not collected; only Compose project/service labels are retained. No host file contents or private media are scraped.

`ocms-telemetry` is an internal Docker network. OTLP port 4318 is not published on the host. Alloy's health/metrics UI is available only through `127.0.0.1:12345`; use an SSH tunnel for remote access. Alloy also has an egress network for HTTPS to Grafana Cloud.

cAdvisor requires privileged access, the Docker socket and host filesystem mounts, following the official Alloy Docker deployment. This is a dedicated monitoring container. Limits are CPU 0.5, memory 512 MiB (swap-inclusive 768 MiB); the OTLP memory limiter is 320 MiB and queues are bounded. Cloud export retries for up to five minutes, after which a failing batch may be lost; the sending queue is in memory, not a durable archive. Docker log offsets are stored in `alloy-data`. This is monitoring, not a log backup.

## Host credentials

Create `/etc/komodo/secrets/observability/cloud.env` with mode 600, owned by root. Use `sudoedit` to enter the values from Grafana Cloud's **OpenTelemetry** connection page. The endpoint is the base gateway URL ending in `/otlp`, not `/v1/metrics`. The username is the instance ID shown by that page. The access policy token requires `metrics:write`, `logs:write`, and `traces:write` for the destination stack.

```dotenv
GRAFANA_CLOUD_OTLP_ENDPOINT=https://otlp-gateway-REGION.grafana.net/otlp
GRAFANA_CLOUD_INSTANCE_ID=REPLACE_FROM_CLOUD
GRAFANA_CLOUD_TOKEN=REPLACE_WITH_INGESTION_TOKEN
```

The host file is a Komodo additional environment file with `track = false`. Never commit real values or paste them into logs. Authentication for Grafana MCP is separate from Alloy ingestion.

## Deployment

The Resource Sync updates definitions without deploying stacks. The `usbharu-lab-gitops` procedure then deploys observability, OCMS, and management in that order. This creates the shared telemetry network before OCMS joins it. For the first installation, run the Resource Sync first, then start a fresh procedure so that it uses the updated stage order.

The OCMS configuration points each instrumented service to `http://alloy:4318` and marks the environment as `production`. There is no DB migration, application image update or model download. Compose recreates the affected application containers to apply the environment/network changes. Models, DB, R2 objects and crypto keys are preserved.

Alloy configuration changes are tracked through Komodo's `config_files` and restart only Alloy. Follow the procedure result and then check:

```sh
curl -f http://127.0.0.1:12345/-/ready
curl -fsS http://127.0.0.1:12345/metrics
```

Check exporter success/failure counters and query recent data in Cloud. A healthy receiver alone does not prove successful authentication or export. The dashboards intentionally show no data for missing/idle signals rather than claiming zero failures.

## Dashboards

The two dashboard JSON files use datasource variables for Prometheus, Loki and Tempo:

- `usbharulab-infra.json`: Ubuntu saturation, container CPU/memory/throttling, Komodo load, Docker journal/Komodo logs, Alloy exporter failures.
- `usbharulab-ocms.json`: HTTP throughput/5xx/p95, completed/failed jobs, processing duration, transfers, container CPU, logs and slow traces.

Metrics use the fixed `lab_host="ocms-ubuntu"` label; logs and traces use the resource namespace `usbharu-lab`. OCMS does not expose a queue-length gauge, so these dashboards do not invent one. The job counter records completed attempts and final failures/retries according to the app, and is not an exact DB backlog count. Runtime operations and Go HTTP handling expose different instrumentation; HTTP panels use the Go histogram. Existing application code reports a fixed `service.version`, so use container images to identify deployed releases.

Import the JSON from the Grafana UI or publish with the helper. For automatic publishing, save a Grafana service-account token able to read data sources and create/update dashboards to `/etc/komodo/secrets/observability/grafana-api-token` with mode 600. It is a different token from the Cloud ingestion token. The helper only overwrites dashboard UIDs `usbharulab-infra` and `usbharulab-ocms`; it does not change data sources or other dashboards.

```sh
sudo python3 /etc/komodo/gitops/observability/scripts/publish-dashboards.py
```

Cloud URLs after import:

- https://usbharulab.grafana.net/d/usbharulab-infra
- https://usbharulab.grafana.net/d/usbharulab-ocms

## Validation

Validate Compose with dummy credentials and run `alloy validate` using the pinned image before deployment. Local smoke tests can send generated OTLP data to an isolated receiver; do not inject fixtures into production. Validation of the configuration and local export is separate from Cloud authentication, actual dashboard queries and production resource usage.

Official references:

- https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.exporter.cadvisor/
- https://grafana.com/docs/alloy/latest/reference/components/otelcol/otelcol.exporter.otlphttp/
- https://grafana.com/docs/grafana-cloud/send-data/otlp/otlp-format-considerations/

Validation performed on 2026-10-28: the pinned Alloy image accepted `alloy validate`; the isolated Linux Docker smoke test reported healthy components, collected Ubuntu and Docker metrics with the expected host/Compose labels, received generated metrics/logs/traces over OTLP/HTTP, and exported all three signals to a mock OTLP endpoint. A generated Komodo container log was exported with `service.name=komodo-core`. The shell readiness probe returned success. Smoke-test memory was approximately 265 MiB, which is a single local observation rather than a production bound. Both Compose files, TOML deployment order, dashboard structure, helper Python syntax and `git diff --check` passed. Cloud authentication, Cloud queries, actual dashboard import/rendering and production resource usage remain unverified until credentials are supplied.
