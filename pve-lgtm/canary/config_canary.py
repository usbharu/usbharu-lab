#!/usr/bin/env python3
"""Detect drift and tampering in the Grafana alerting configuration.

The canary reads the Grafana API with the local admin account and publishes:

* counts for alert rules, contact points, routes, silences, datasources,
  dashboards and mute timings;
* a hash of each category compared with a baseline written after a deliberate
  ``apply-grafana-alerting.sh`` run;
* silence creation and maximum silence duration.

A UI edit, rule deletion, contact-point URL change or long silence then shows
up as a metric instead of silently disabling notifications.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import otlp  # noqa: E402

SERVICE = "lgtm-config-canary"
HOST = os.environ.get("CANARY_HOST", "lgtm")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")
ENV_FILE = os.environ.get("GRAFANA_ENV_FILE", "/opt/lgtm/.env")
BASELINE_FILE = Path(
    os.environ.get("CONFIG_CANARY_BASELINE", "/etc/lgtm-canary/baseline.json")
)
STATE_FILE = Path(
    os.environ.get("CONFIG_CANARY_STATE", "/var/lib/lgtm-canary/config-state.json")
)


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


def grafana_get(path: str):
    request = urllib.request.Request(
        f"{GRAFANA_URL}{path}",
        headers={"Authorization": f"Basic {AUTH}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def canonical(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def route_count(route: dict | None) -> int:
    if not route:
        return 0
    return 1 + sum(route_count(child) for child in route.get("routes") or [])


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    tmp.replace(STATE_FILE)


def fetch_config() -> dict:
    rules = grafana_get("/api/v1/provisioning/alert-rules")
    contact_points = grafana_get("/api/v1/provisioning/contact-points")
    policies = grafana_get("/api/v1/provisioning/policies")
    mute_timings = grafana_get("/api/v1/provisioning/mute-timings")
    datasources = grafana_get("/api/datasources")
    dashboards = grafana_get("/api/search?type=dash-db&limit=1000")
    silences = grafana_get("/api/alertmanager/grafana/api/v2/silences")
    return {
        "alert_rules": rules,
        "contact_points": contact_points,
        "policies": policies,
        "mute_timings": mute_timings,
        "datasources": datasources,
        "dashboards": dashboards,
        "silences": silences,
    }


def active_silences(silences: list[dict]) -> list[dict]:
    now = time.time()
    active = []
    for silence in silences:
        ends_at = silence.get("endsAt")
        if not ends_at:
            active.append(silence)
            continue
        try:
            # The API returns RFC3339 with a trailing Z.
            from datetime import datetime

            parsed = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
            if parsed.timestamp() > now:
                active.append(silence)
        except ValueError:
            active.append(silence)
    return active


def silence_duration(silence: dict) -> float:
    starts_at = silence.get("startsAt")
    ends_at = silence.get("endsAt")
    if not starts_at or not ends_at:
        return 0.0
    try:
        from datetime import datetime

        start = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
        return max(0.0, (end - start).total_seconds())
    except ValueError:
        return 0.0


def build_baseline(config: dict) -> dict:
    return {
        "alert_rules": fingerprint(config["alert_rules"]),
        "contact_points": fingerprint(config["contact_points"]),
        "policies": fingerprint(config["policies"]),
        "mute_timings": fingerprint(config["mute_timings"]),
        "datasources": fingerprint(config["datasources"]),
        "dashboards": fingerprint(config["dashboards"]),
    }


def update_baseline(config: dict) -> None:
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BASELINE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(build_baseline(config), sort_keys=True, indent=2) + "\n"
    )
    tmp.replace(BASELINE_FILE)
    print(f"baseline written to {BASELINE_FILE}")


def load_baseline() -> dict:
    try:
        return json.loads(BASELINE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def emit(config: dict, api_ok: bool) -> None:
    now = time.time()
    rules = config["alert_rules"]
    contact_points = config["contact_points"]
    policies = config["policies"]
    silences = active_silences(config["silences"])
    baseline = load_baseline()
    current = build_baseline(config) if api_ok else {}

    state = load_state()
    seen = set(state.get("silence_ids") or [])
    current_ids = {silence.get("id") for silence in silences if silence.get("id")}
    created_total = int(state.get("silences_created_total") or 0)
    new_ids = current_ids - seen
    if api_ok:
        created_total += len(new_ids)
    state["silence_ids"] = sorted(current_ids)
    state["silences_created_total"] = created_total
    save_state(state)

    metrics = [
        {
            "name": "lgtm_config_canary_timestamp_seconds",
            "value": now,
            "unit": "s",
        },
        {"name": "lgtm_config_api_ok", "value": 1 if api_ok else 0},
        {
            "name": "lgtm_config_baseline_present",
            "value": 1 if baseline else 0,
        },
    ]
    if api_ok:
        metrics.extend(
            [
                {
                    "name": "lgtm_alert_rules_total",
                    "value": len(rules),
                },
                {
                    "name": "lgtm_alert_rules_paused",
                    "value": sum(1 for rule in rules if rule.get("isPaused")),
                },
                {
                    "name": "lgtm_contact_points_total",
                    "value": len(contact_points),
                },
                {
                    "name": "lgtm_notification_policy_routes_total",
                    "value": route_count(policies),
                },
                {
                    "name": "lgtm_silences_total",
                    "value": len(silences),
                },
                {
                    "name": "lgtm_silence_max_duration_seconds",
                    "value": max((silence_duration(s) for s in silences), default=0.0),
                    "unit": "s",
                },
                {
                    "name": "lgtm_silences_created_total",
                    "value": created_total,
                },
                {
                    "name": "lgtm_datasources_total",
                    "value": len(config["datasources"]),
                },
                {
                    "name": "lgtm_dashboards_total",
                    "value": len(config["dashboards"]),
                },
            ]
        )
        for kind, value in current.items():
            metrics.append(
                {
                    "name": "lgtm_config_matches_baseline",
                    "value": 1 if baseline.get(kind) == value else 0,
                    "attributes": {"kind": kind},
                }
            )

    otlp.push_metrics(SERVICE, HOST, metrics)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write the current API state as the trusted baseline",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="fetch and print a summary without pushing metrics",
    )
    args = parser.parse_args()

    try:
        config = fetch_config()
        api_ok = True
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Grafana API error: {exc}", file=sys.stderr)
        config = {
            "alert_rules": [],
            "contact_points": [],
            "policies": {},
            "mute_timings": [],
            "datasources": [],
            "dashboards": [],
            "silences": [],
        }
        api_ok = False

    if api_ok and args.update_baseline:
        update_baseline(config)

    if args.check_only:
        print(
            json.dumps(
                {
                    "api_ok": api_ok,
                    "alert_rules": len(config["alert_rules"]),
                    "contact_points": len(config["contact_points"]),
                    "routes": route_count(config["policies"]),
                    "silences": len(active_silences(config["silences"])),
                    "datasources": len(config["datasources"]),
                    "dashboards": len(config["dashboards"]),
                },
                indent=2,
            )
        )
        return 0 if api_ok else 1

    emit(config, api_ok)
    return 0 if api_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
