#!/usr/bin/env python3
"""Send and verify the LGTM ingestion canary.

Every run sends one metric, one log line and one trace through the same OTLP
collector used by production, then checks that the previous samples are visible
through Grafana's datasource proxy.  The visibility gauges are pushed to Mimir
and evaluated by the Lgtm*Canary rules.

Run from a systemd timer on LXC 200.  The Grafana admin password is read from
/opt/lgtm/.env and never logged.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import otlp  # noqa: E402

SERVICE = "lgtm-canary"
HOST = os.environ.get("CANARY_HOST", "lgtm")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")
ENV_FILE = os.environ.get("GRAFANA_ENV_FILE", "/opt/lgtm/.env")
STATE_DIR = os.environ.get("CANARY_STATE_DIR", "/var/lib/lgtm-canary")


def read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


ENV = read_env(ENV_FILE)
ADMIN_USER = ENV.get("GF_SECURITY_ADMIN_USER", "admin")
ADMIN_PASSWORD = ENV.get("GF_SECURITY_ADMIN_PASSWORD", "")
AUTH = base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode()).decode()


def grafana_get(path: str) -> dict:
    request = urllib.request.Request(
        f"{GRAFANA_URL}{path}",
        headers={"Authorization": f"Basic {AUTH}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def query_loki() -> bool:
    now_ns = time.time_ns()
    start_ns = now_ns - 900 * 1_000_000_000
    query = urllib.parse.urlencode(
        {
            "query": '{service_name="lgtm-canary"}',
            "limit": "1",
            "start": str(start_ns),
            "end": str(now_ns),
        }
    )
    try:
        payload = grafana_get(
            f"/api/datasources/proxy/uid/loki/loki/api/v1/query_range?{query}"
        )
        return bool(payload.get("data", {}).get("result"))
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        return False


def query_tempo() -> bool:
    now = int(time.time())
    query = urllib.parse.urlencode(
        {
            "q": '{ resource.service.name = "lgtm-canary" }',
            "limit": "1",
            "start": str(now - 900),
            "end": str(now),
        }
    )
    try:
        payload = grafana_get(
            f"/api/datasources/proxy/uid/tempo/api/search?{query}"
        )
        return bool(payload.get("traces"))
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        return False


def query_mimir() -> bool:
    query = urllib.parse.urlencode(
        {"query": "time() - max(lgtm_canary_timestamp_seconds)", "time": str(int(time.time()))}
    )
    try:
        payload = grafana_get(
            f"/api/datasources/proxy/uid/mimir/api/v1/query?{query}"
        )
        result = payload.get("data", {}).get("result") or []
        if not result:
            return False
        value = float(result[0]["value"][1])
        return value < 180
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError):
        return False


def main() -> int:
    os.makedirs(STATE_DIR, exist_ok=True)
    now = time.time()
    errors: list[str] = []

    try:
        otlp.push_metrics(
            SERVICE,
            HOST,
            [{"name": "lgtm_canary_timestamp_seconds", "value": now, "unit": "s"}],
        )
    except Exception as exc:  # noqa: BLE001 - keep the canary running
        errors.append(f"metric push: {exc}")

    try:
        otlp.push_log(SERVICE, HOST, "lgtm ingestion canary")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"log push: {exc}")

    try:
        otlp.push_trace(SERVICE, HOST, "lgtm-ingestion-canary")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"trace push: {exc}")

    # The samples from this run may not be queryable yet.  The previous run is
    # sufficient for a 15 minute freshness check.
    mimir_visible = query_mimir()
    loki_visible = query_loki()
    tempo_visible = query_tempo()

    try:
        otlp.push_metrics(
            SERVICE,
            HOST,
            [
                {
                    "name": "lgtm_canary_mimir_visible",
                    "value": 1 if mimir_visible else 0,
                },
                {
                    "name": "lgtm_canary_log_visible",
                    "value": 1 if loki_visible else 0,
                },
                {
                    "name": "lgtm_canary_trace_visible",
                    "value": 1 if tempo_visible else 0,
                },
                {
                    "name": "lgtm_canary_last_run_timestamp_seconds",
                    "value": now,
                    "unit": "s",
                },
            ],
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"visibility push: {exc}")

    if errors:
        print("; ".join(errors), file=sys.stderr)
        return 1
    if not (mimir_visible and loki_visible and tempo_visible):
        print(
            "canary not visible: "
            f"mimir={mimir_visible} loki={loki_visible} tempo={tempo_visible}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
