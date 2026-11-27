#!/usr/bin/env python3
"""Create and verify a daily logical backup of the OCMS PostgreSQL database.

The dump is written in custom format, verified with ``pg_restore --list`` and
then published as metrics.  A failed run publishes a failure timestamp and
``postgres_backup_restore_verified=0``; the last success timestamp is left
untouched so the stale-backup alert eventually fires.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import otlp  # noqa: E402

SERVICE = "postgres-backup"
HOST = os.environ.get("CANARY_HOST", "db-pve3")
BACKUP_DIR = Path(os.environ.get("PG_BACKUP_DIR", "/var/backups/postgresql"))
DATABASE = os.environ.get("PG_BACKUP_DATABASE", "ocms")
RETENTION_DAYS = int(os.environ.get("PG_BACKUP_RETENTION_DAYS", "7"))
PG_BIN = os.environ.get("PG_BIN", "/usr/lib/postgresql/17/bin")
PG_USER = os.environ.get("PG_BACKUP_USER", "postgres")
PG_PORT = os.environ.get("PG_BACKUP_PORT", "5434")


def push(metrics: list[dict]) -> None:
    otlp.push_metrics(SERVICE, HOST, metrics)


def run_as_postgres(args: list[str], stdout=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sudo", "-u", PG_USER, *args],
        stdout=stdout,
        stderr=subprocess.PIPE,
        text=True,
        timeout=3600,
        check=False,
    )


def main() -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    started = time.time()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = BACKUP_DIR / f"{DATABASE}-{stamp}.dump"
    tmp_path = final_path.with_suffix(".dump.tmp")

    dump = None
    try:
        with open(tmp_path, "wb") as output:
            dump = run_as_postgres(
                [
                    f"{PG_BIN}/pg_dump",
                    "-Fc",
                    "--no-owner",
                    "--no-privileges",
                    "-p",
                    PG_PORT,
                    "-d",
                    DATABASE,
                ],
                stdout=output,
            )
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        print(f"cannot write backup: {exc}", file=sys.stderr)
        push(
            [
                {"name": "postgres_backup_restore_verified", "value": 0},
                {
                    "name": "postgres_backup_last_failure_timestamp_seconds",
                    "value": time.time(),
                    "unit": "s",
                },
            ]
        )
        return 1
    if dump is None or dump.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        message = ((dump.stderr if dump else "") or "").strip().splitlines()
        print(
            f"pg_dump failed: {message[-1] if message else 'unknown error'}",
            file=sys.stderr,
        )
        push(
            [
                {"name": "postgres_backup_restore_verified", "value": 0},
                {
                    "name": "postgres_backup_last_failure_timestamp_seconds",
                    "value": time.time(),
                    "unit": "s",
                },
            ]
        )
        return 1

    os.chmod(tmp_path, 0o600)
    verify = subprocess.run(
        [f"{PG_BIN}/pg_restore", "--list", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if verify.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        message = (verify.stderr or verify.stdout).strip().splitlines()
        print(
            f"pg_restore --list failed: {message[-1] if message else verify.returncode}",
            file=sys.stderr,
        )
        push(
            [
                {"name": "postgres_backup_restore_verified", "value": 0},
                {
                    "name": "postgres_backup_last_failure_timestamp_seconds",
                    "value": time.time(),
                    "unit": "s",
                },
            ]
        )
        return 1

    tmp_path.replace(final_path)
    duration = time.time() - started
    size = final_path.stat().st_size
    push(
        [
            {
                "name": "postgres_backup_last_success_timestamp_seconds",
                "value": time.time(),
                "unit": "s",
            },
            {
                "name": "postgres_backup_last_duration_seconds",
                "value": duration,
                "unit": "s",
            },
            {
                "name": "postgres_backup_last_size_bytes",
                "value": size,
                "unit": "By",
            },
            {"name": "postgres_backup_restore_verified", "value": 1},
        ]
    )

    cutoff = time.time() - RETENTION_DAYS * 86400
    for old in BACKUP_DIR.glob(f"{DATABASE}-*.dump"):
        try:
            if old.is_file() and old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError as exc:
            print(f"failed to remove old backup {old}: {exc}", file=sys.stderr)

    print(f"backup written: {final_path} ({size} bytes, {duration:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
