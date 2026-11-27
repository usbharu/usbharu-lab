#!/usr/bin/env python3
"""External dead-man's switch for the home LGTM stack.

This runs on the ocms-ubuntu VPS, outside pve2 / LXC 200.  It probes the public
entrypoints and the WireGuard-reachable OTLP endpoint, and sends a Discord
message directly when a probe fails twice in a row.  It does not depend on
Grafana, Mimir, Loki, Tempo or the home network.

The same results are pushed to Mimir when the stack is reachable, so the
internal dashboards can show the external view too.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import otlp  # noqa: E402

SERVICE = "external-monitor"
HOST = os.environ.get("MONITOR_HOST", "ocms-ubuntu")
ENV_FILE = os.environ.get("MONITOR_ENV_FILE", "/etc/lab-external-monitor.env")
STATE_FILE = Path(
    os.environ.get("MONITOR_STATE_FILE", "/var/lib/lab-external-monitor/state.json")
)
FAIL_THRESHOLD = int(os.environ.get("MONITOR_FAIL_THRESHOLD", "2"))
REPEAT_SECONDS = int(os.environ.get("MONITOR_REPEAT_SECONDS", str(6 * 3600)))
TIMEOUT = float(os.environ.get("MONITOR_TIMEOUT_SECONDS", "15"))

CHECKS = {
    "grafana": {
        "kind": "http",
        "url": "https://grafana.usbharu.dev/api/health",
        "expect": 200,
        "contains": '"database":"ok"',
        "description": "Grafana / LXC 200 public health",
    },
    "otlp": {
        "kind": "tcp",
        "host": "192.168.1.240",
        "port": 4318,
        "description": "OTel Collector OTLP/HTTP over WireGuard",
    },
    "komodo": {
        "kind": "http",
        "url": "https://komodo.usbharu.dev/",
        "expect": 200,
        "description": "Komodo Core",
    },
    "pocket-id": {
        "kind": "http",
        "url": "https://id.usbharu.dev/",
        "expect": (200, 302),
        "description": "Pocket ID",
    },
    "ocms": {
        "kind": "http",
        "url": "https://ocms.usbharu.dev/healthz",
        "expect": 200,
        "description": "OCMS BFF health",
    },
}


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
DISCORD_WEBHOOK = ENV.get("DISCORD_WEBHOOK_URL", "")
GRAFANA_TOKEN = ENV.get("GRAFANA_MONITOR_TOKEN", "")

if GRAFANA_TOKEN:
    auth_headers = {"Authorization": f"Bearer {GRAFANA_TOKEN}"}
    for name, description in (
        ("mimir", "Grafana -> Mimir datasource health"),
        ("loki", "Grafana -> Loki datasource health"),
        ("tempo", "Grafana -> Tempo datasource health"),
    ):
        CHECKS[name] = {
            "kind": "http",
            "url": f"https://grafana.usbharu.dev/api/datasources/uid/{name}/health",
            "expect": 200,
            "contains": '"status":"OK"',
            "headers": auth_headers,
            "description": description,
        }


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


def check_http(spec: dict) -> tuple[bool, str]:
    request = urllib.request.Request(
        spec["url"], headers=spec.get("headers") or {}, method="GET"
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            expected = spec.get("expect", 200)
            expected_values = expected if isinstance(expected, tuple) else (expected,)
            if response.status not in expected_values:
                return False, f"HTTP {response.status}"
            contains = spec.get("contains")
            if contains and contains not in body.replace(" ", ""):
                return False, f"HTTP {response.status} but body did not match"
            return True, f"HTTP {response.status}"
    except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as exc:
        return False, str(exc)


def check_tcp(spec: dict) -> tuple[bool, str]:
    try:
        with socket.create_connection((spec["host"], spec["port"]), timeout=TIMEOUT):
            return True, "connected"
    except OSError as exc:
        return False, str(exc)


def notify(title: str, description: str, color: int) -> bool:
    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK_URL is not configured", file=sys.stderr)
        return False
    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        ]
    }
    request = urllib.request.Request(
        DISCORD_WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Discord notification failed: {exc}", file=sys.stderr)
        return False


def main() -> int:
    state = load_state()
    now = time.time()
    metrics: list[dict] = []
    failures: list[str] = []
    notify_items: list[tuple[str, dict, str, dict]] = []
    recovered_items: list[tuple[str, str, dict]] = []

    for name, spec in CHECKS.items():
        if spec["kind"] == "http":
            ok, detail = check_http(spec)
        else:
            ok, detail = check_tcp(spec)
        entry = state.setdefault(name, {"failures": 0, "notified": False, "last_notify": 0})
        if ok:
            if entry.get("notified"):
                recovered_items.append((name, detail, entry))
            entry["failures"] = 0
        else:
            entry["failures"] = int(entry.get("failures") or 0) + 1
            failures.append(f"`{name}`: {detail}")
            should_notify = entry["failures"] >= FAIL_THRESHOLD and (
                not entry.get("notified")
                or now - float(entry.get("last_notify") or 0) >= REPEAT_SECONDS
            )
            if should_notify:
                notify_items.append((name, spec, detail, entry))
        metrics.append(
            {
                "name": "external_monitor_check_success",
                "value": 1 if ok else 0,
                "attributes": {"check": name},
            }
        )
        metrics.append(
            {
                "name": "external_monitor_consecutive_failures",
                "value": int(entry.get("failures") or 0),
                "attributes": {"check": name},
            }
        )

    if notify_items:
        lines = ["**監視元**: ocms-ubuntu VPS (自宅LAN外)", ""]
        for name, spec, detail, entry in notify_items:
            lines.extend(
                [
                    f"**{name}**: {spec['description']}",
                    f"- 結果: {detail}",
                    f"- 連続失敗: {entry['failures']} 回",
                    "",
                ]
            )
        if notify("外部監視: チェック失敗", "\n".join(lines), 0xD32F2F):
            for _, _, _, entry in notify_items:
                entry["notified"] = True
                entry["last_notify"] = now

    if recovered_items:
        lines = ["**監視元**: ocms-ubuntu VPS (自宅LAN外)", ""]
        for name, detail, _ in recovered_items:
            lines.append(f"`{name}`: {detail}")
        if notify("外部監視: 復旧", "\n".join(lines), 0x2E7D32):
            for _, _, entry in recovered_items:
                entry["notified"] = False
                entry["last_notify"] = 0

    state["last_run"] = now
    save_state(state)

    metrics.append(
        {
            "name": "external_monitor_last_run_timestamp_seconds",
            "value": now,
            "unit": "s",
        }
    )
    try:
        otlp.push_metrics(SERVICE, HOST, metrics)
    except Exception as exc:  # noqa: BLE001
        # The probe result is already delivered to Discord when it matters.
        print(f"OTLP push error: {exc}", file=sys.stderr)

    if failures:
        print("failed checks: " + "; ".join(failures), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
