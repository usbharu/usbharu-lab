#!/usr/bin/env python3
"""Publish Komodo GitOps success and schedule state to Mimir.

Komodo's procedure list exposes the last successful run (``last_run_at``) and
the current state.  A stale timestamp catches a scheduler that has stopped even
when no failure notification was sent.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import otlp  # noqa: E402

SERVICE = "lgtm-gitops-canary"
HOST = os.environ.get("CANARY_HOST", "lgtm")
KOMODO_URL = os.environ.get("KOMODO_URL", "https://komodo.usbharu.dev").rstrip("/")
ENV_FILE = os.environ.get("LGTM_ENV_FILE", "/opt/lgtm/.env")
PROCEDURE = os.environ.get("GITOPS_PROCEDURE", "usbharu-lab-gitops")


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
API_KEY = ENV.get("KOMODO_API_KEY", "")
API_SECRET = ENV.get("KOMODO_API_SECRET", "")


def komodo_post(method: str, body: dict):
    request = urllib.request.Request(
        f"{KOMODO_URL}/read/{method}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": API_KEY,
            "X-Api-Secret": API_SECRET,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def state_value(state: str) -> dict[str, float]:
    known = {"ok", "failed", "running", "unknown"}
    value = state.lower() if state else "unknown"
    if value not in known:
        value = "unknown"
    return {f"state_{value}": 1.0}


def main() -> int:
    now = time.time()
    metrics = [
        {
            "name": "gitops_canary_timestamp_seconds",
            "value": now,
            "unit": "s",
        }
    ]
    api_ok = True
    try:
        procedures = komodo_post("ListProcedures", {"limit": 0})
        item = next(
            (procedure for procedure in procedures if procedure.get("name") == PROCEDURE),
            None,
        )
        if item is None:
            raise RuntimeError(f"procedure {PROCEDURE!r} not found")
        info = item.get("info") or {}
        last_run_at = info.get("last_run_at")
        next_run = info.get("next_scheduled_run")
        state = str(info.get("state") or "unknown")
        metrics.append({"name": "gitops_api_ok", "value": 1})
        metrics.append({"name": "gitops_state", "value": 1, "attributes": {"state": state.lower()}})
        if last_run_at:
            metrics.append(
                {
                    "name": "gitops_last_success_timestamp_seconds",
                    "value": float(last_run_at) / 1000.0,
                    "unit": "s",
                }
            )
        if next_run:
            metrics.append(
                {
                    "name": "gitops_next_scheduled_run_timestamp_seconds",
                    "value": float(next_run) / 1000.0,
                    "unit": "s",
                }
            )

        procedure = komodo_post("GetProcedure", {"procedure": PROCEDURE})
        config = procedure.get("config") or {}
        metrics.append(
            {
                "name": "gitops_schedule_enabled",
                "value": 1 if config.get("schedule_enabled") else 0,
            }
        )
        metrics.append(
            {
                "name": "gitops_webhook_enabled",
                "value": 1 if config.get("webhook_enabled") else 0,
            }
        )
    except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
        print(f"Komodo API error: {exc}", file=sys.stderr)
        api_ok = False
        metrics.append({"name": "gitops_api_ok", "value": 0})
        metrics.append(
            {
                "name": "gitops_schedule_enabled",
                "value": 0,
            }
        )

    try:
        otlp.push_metrics(SERVICE, HOST, metrics)
    except Exception as exc:  # noqa: BLE001
        print(f"OTLP push error: {exc}", file=sys.stderr)
        return 1
    return 0 if api_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
