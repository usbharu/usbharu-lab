"""Minimal dependency-free OTLP/HTTP JSON sender for the LGTM canaries.

The scripts in this directory run on hosts that only guarantee Python 3 and
curl.  They deliberately avoid the OpenTelemetry SDK so the canary does not
depend on the same libraries as the monitored applications.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "http://127.0.0.1:4318"
DEFAULT_TIMEOUT = 10.0


def _endpoint() -> str:
    return os.environ.get("OTLP_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/")


def _string(value: str) -> dict:
    return {"stringValue": value}


def _resource(service: str, host: str, extra: dict[str, str] | None = None) -> dict:
    attributes = [
        {"key": "service.name", "value": _string(service)},
        {"key": "host.name", "value": _string(host)},
    ]
    for key, value in (extra or {}).items():
        attributes.append({"key": key, "value": _string(value)})
    return {"attributes": attributes}


def _post(path: str, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{_endpoint()}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        if response.status >= 300:
            raise RuntimeError(f"OTLP {path} returned HTTP {response.status}")


def push_metrics(
    service: str,
    host: str,
    metrics: list[dict],
    extra_resource: dict[str, str] | None = None,
) -> None:
    """Push gauge metrics.

    ``metrics`` is a list of ``{"name": str, "value": float, "unit": str,
    "attributes": {str: str}}``.  Every metric is sent as a gauge, which is
    enough for the canary freshness and configuration fingerprints.
    """
    now = str(__import__("time").time_ns())
    points = []
    for metric in metrics:
        attributes = [
            {"key": key, "value": _string(value)}
            for key, value in (metric.get("attributes") or {}).items()
        ]
        point = {
            "timeUnixNano": now,
            "asDouble": float(metric["value"]),
        }
        if attributes:
            point["attributes"] = attributes
        points.append(
            {
                "name": metric["name"],
                "unit": metric.get("unit", "1"),
                "gauge": {"dataPoints": [point]},
            }
        )
    _post(
        "/v1/metrics",
        {
            "resourceMetrics": [
                {
                    "resource": _resource(service, host, extra_resource),
                    "scopeMetrics": [
                        {"scope": {"name": service}, "metrics": points}
                    ],
                }
            ]
        },
    )


def push_log(service: str, host: str, body: str, severity: str = "INFO") -> None:
    now = str(__import__("time").time_ns())
    _post(
        "/v1/logs",
        {
            "resourceLogs": [
                {
                    "resource": _resource(service, host),
                    "scopeLogs": [
                        {
                            "scope": {"name": service},
                            "logRecords": [
                                {
                                    "timeUnixNano": now,
                                    "severityText": severity,
                                    "severityNumber": 9,
                                    "body": _string(body),
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )


def push_trace(service: str, host: str, name: str) -> None:
    import secrets
    import time

    now = time.time_ns()
    _post(
        "/v1/traces",
        {
            "resourceSpans": [
                {
                    "resource": _resource(service, host),
                    "scopeSpans": [
                        {
                            "scope": {"name": service},
                            "spans": [
                                {
                                    "traceId": secrets.token_hex(16),
                                    "spanId": secrets.token_hex(8),
                                    "name": name,
                                    "kind": 1,
                                    "startTimeUnixNano": str(now),
                                    "endTimeUnixNano": str(now + 1_000_000),
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )


def add_cli_metrics(parser) -> None:  # pragma: no cover - helper for scripts
    parser.add_argument(
        "--otlp-endpoint",
        default=os.environ.get("OTLP_ENDPOINT", DEFAULT_ENDPOINT),
        help="OTLP/HTTP base URL (default: %(default)s)",
    )


def set_endpoint(endpoint: str) -> None:
    os.environ["OTLP_ENDPOINT"] = endpoint
