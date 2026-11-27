#!/usr/bin/env python3
"""Publish Cloudflare R2 bucket and multipart-upload metrics.

R2 is the Tempo backend.  A failed multipart upload does not show up in Tempo's
own backend size metric, so this canary counts unfinished uploads and tracks
the oldest one.  Bucket size is refreshed hourly because listing a bucket is a
relatively expensive operation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import otlp  # noqa: E402

SERVICE = "lgtm-r2-canary"
HOST = os.environ.get("CANARY_HOST", "lgtm")
ENV_FILE = os.environ.get("R2_ENV_FILE", "/root/lgtm-s3.env")
STATE_FILE = Path(os.environ.get("R2_STATE_FILE", "/var/lib/lgtm-canary/r2-state.json"))
SIZE_INTERVAL = int(os.environ.get("R2_SIZE_INTERVAL_SECONDS", str(3600)))
TIMEOUT = int(os.environ.get("R2_TIMEOUT_SECONDS", "90"))


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
AWS_ENV = {
    "AWS_ACCESS_KEY_ID": ENV.get("S3_ACCESS_KEY", ""),
    "AWS_SECRET_ACCESS_KEY": ENV.get("S3_SECRET_KEY", ""),
    "AWS_DEFAULT_REGION": ENV.get("S3_REGION", "auto"),
    "AWS_EC2_METADATA_DISABLED": "true",
}
ENDPOINT = ENV.get("S3_ENDPOINT", "")
REGION = ENV.get("S3_REGION", "auto")
BUCKETS = {
    "mimir": ENV.get("S3_BUCKET_MIMIR", ""),
    "loki": ENV.get("S3_BUCKET_LOKI", ""),
    "tempo": ENV.get("S3_BUCKET_TEMPO", ""),
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


def aws(args: list[str]) -> str:
    command = ["aws", "--endpoint-url", ENDPOINT, "--region", REGION, *args]
    result = subprocess.run(
        command,
        env={**os.environ, **AWS_ENV},
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(message[-1] if message else f"aws exited {result.returncode}")
    return result.stdout


def multipart_stats(bucket: str) -> tuple[int, float]:
    count = 0
    oldest = 0.0
    key_marker = None
    upload_marker = None
    while True:
        args = ["s3api", "list-multipart-uploads", "--bucket", bucket, "--output", "json"]
        if key_marker:
            args.extend(["--key-marker", key_marker])
        if upload_marker:
            args.extend(["--upload-id-marker", upload_marker])
        payload = json.loads(aws(args) or "{}")
        for upload in payload.get("Uploads") or []:
            count += 1
            initiated = upload.get("Initiated")
            if isinstance(initiated, (int, float)):
                timestamp = float(initiated)
            elif isinstance(initiated, str):
                try:
                    from datetime import datetime

                    timestamp = datetime.fromisoformat(
                        initiated.replace("Z", "+00:00")
                    ).timestamp()
                except ValueError:
                    timestamp = 0.0
            else:
                timestamp = 0.0
            if timestamp and (oldest == 0.0 or timestamp < oldest):
                oldest = timestamp
        if not payload.get("IsTruncated"):
            break
        key_marker = payload.get("NextKeyMarker")
        upload_marker = payload.get("NextUploadIdMarker")
        if not key_marker:
            break
    return count, max(0.0, time.time() - oldest) if oldest else 0.0


def bucket_size(bucket: str) -> tuple[int, int]:
    output = aws(["s3", "ls", "--summarize", "--recursive", f"s3://{bucket}"])
    objects = 0
    size = 0
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Total Objects:"):
            objects = int(line.split(":", 1)[1].strip())
        elif line.startswith("Total Size:"):
            size = int(line.split(":", 1)[1].strip())
    return objects, size


def main() -> int:
    if not ENDPOINT or not all(BUCKETS.values()):
        print("R2 environment is incomplete", file=sys.stderr)
        return 2

    state = load_state()
    last_size = float(state.get("last_size_at") or 0)
    measure_size = (time.time() - last_size) >= SIZE_INTERVAL
    metrics: list[dict] = []
    errors: list[str] = []
    api_ok = True

    for name, bucket in BUCKETS.items():
        try:
            count, oldest_age = multipart_stats(bucket)
            metrics.extend(
                [
                    {
                        "name": "r2_multipart_upload_count",
                        "value": count,
                        "attributes": {"bucket": name},
                    },
                    {
                        "name": "r2_multipart_oldest_age_seconds",
                        "value": oldest_age,
                        "unit": "s",
                        "attributes": {"bucket": name},
                    },
                ]
            )
        except (RuntimeError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            api_ok = False
            errors.append(f"{name} multipart: {exc}")

        if measure_size and api_ok:
            try:
                objects, size = bucket_size(bucket)
                metrics.extend(
                    [
                        {
                            "name": "r2_bucket_object_count",
                            "value": objects,
                            "attributes": {"bucket": name},
                        },
                        {
                            "name": "r2_bucket_size_bytes",
                            "value": size,
                            "unit": "By",
                            "attributes": {"bucket": name},
                        },
                    ]
                )
            except (RuntimeError, TimeoutError, ValueError) as exc:
                api_ok = False
                errors.append(f"{name} size: {exc}")

    if measure_size and api_ok:
        state["last_size_at"] = time.time()
    save_state(state)

    metrics.extend(
        [
            {
                "name": "r2_usage_canary_timestamp_seconds",
                "value": time.time(),
                "unit": "s",
            },
            {"name": "r2_usage_api_ok", "value": 1 if api_ok else 0},
        ]
    )

    try:
        otlp.push_metrics(SERVICE, HOST, metrics)
    except Exception as exc:  # noqa: BLE001
        print(f"OTLP push error: {exc}", file=sys.stderr)
        return 1

    if errors:
        print("; ".join(errors), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
