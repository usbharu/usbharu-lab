#!/usr/bin/env python3
"""Publish ZFS scrub and vdev error metrics for pve3.

``zpool status -j`` is available on the ZFS version used by Proxmox VE 9.  It
gives the scrub error count, the scrub start time and per-vdev read/write/
checksum errors without parsing human-readable output.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import otlp  # noqa: E402

SERVICE = "zfs-scrub"
HOST = os.environ.get("CANARY_HOST", "pve3")
POOLS = [pool for pool in os.environ.get("ZFS_POOLS", "hdd").split(",") if pool]


def zpool_status(pool: str) -> dict:
    result = subprocess.run(
        ["zpool", "status", "-j", pool],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(message[-1] if message else f"zpool exited {result.returncode}")
    return json.loads(result.stdout)


def walk_vdevs(vdev: dict, pool: str, metrics: list[dict]) -> None:
    name = vdev.get("name")
    if name:
        for kind in ("read_errors", "write_errors", "checksum_errors"):
            value = vdev.get(kind)
            if value is not None:
                metrics.append(
                    {
                        "name": "zfs_vdev_errors",
                        "value": float(value),
                        "attributes": {
                            "pool": pool,
                            "vdev": name,
                            "kind": kind.removesuffix("_errors"),
                        },
                    }
                )
    for child in (vdev.get("vdevs") or {}).values():
        walk_vdevs(child, pool, metrics)


def main() -> int:
    metrics: list[dict] = []
    errors: list[str] = []
    now = time.time()
    for pool in POOLS:
        try:
            status = zpool_status(pool)
            pool_data = status["pools"][pool]
            scan = pool_data.get("scan_stats") or {}
            metrics.extend(
                [
                    {
                        "name": "zfs_pool_scrub_errors",
                        "value": float(scan.get("errors") or 0),
                        "attributes": {"pool": pool},
                    },
                    {
                        "name": "zfs_pool_scrub_last_timestamp_seconds",
                        "value": float(scan.get("pass_start") or 0),
                        "unit": "s",
                        "attributes": {"pool": pool},
                    },
                    {
                        "name": "zfs_pool_scrub_finished",
                        "value": 1 if str(scan.get("state", "")).upper() == "FINISHED" else 0,
                        "attributes": {"pool": pool},
                    },
                    {
                        "name": "zfs_pool_state",
                        "value": 1 if str(pool_data.get("state", "")).upper() == "ONLINE" else 0,
                        "attributes": {"pool": pool},
                    },
                ]
            )
            root = (pool_data.get("vdevs") or {}).get(pool)
            if root:
                walk_vdevs(root, pool, metrics)
        except (RuntimeError, KeyError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{pool}: {exc}")

    metrics.extend(
        [
            {
                "name": "zfs_scrub_canary_timestamp_seconds",
                "value": now,
                "unit": "s",
            },
            {"name": "zfs_scrub_api_ok", "value": 0 if errors else 1},
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
