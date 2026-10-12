#!/usr/bin/env python3
"""Disposable/offline SQLite backup integrity drill; NEVER authorizes prod stop.

Copies via SQLite's backup API, validates both files, creates an exclusive
0600 backup plus secret-free receipt, and cleans partial output on error.
A successful drill does not prove that a live production database was stopped,
nor old-image application/credential compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def schema_and_integrity(path: Path) -> int:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity failure")
        row = db.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()
        if row is None or int(row[0]) != 7:
            raise RuntimeError("unexpected SQLite schema")
        return int(row[0])


def drill(source: Path, destination: Path) -> dict:
    source = source.absolute()
    destination = destination.absolute()
    if source.is_symlink() or not source.is_file():
        raise ValueError("source must be existing regular SQLite file")
    if source == destination or destination.exists() or destination.is_symlink():
        raise ValueError("destination must not exist")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("destination parent must be an existing directory")
    # Source may contain confidential credentials; do not expose any row data.
    schema_and_integrity(source)
    fd = os.open(destination, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as src:
            src.execute("PRAGMA query_only=ON")
            with sqlite3.connect(str(destination)) as dst:
                src.backup(dst)
        if stat.S_IMODE(destination.stat().st_mode) != 0o600:
            raise RuntimeError("unsafe backup file mode")
        schema_and_integrity(destination)
        return {
            "status": "DISPOSABLE_BACKUP_VERIFIED",
            "schema": 7,
            "bytes": destination.stat().st_size,
            "sha256": digest(destination),
            "release_authorized": False,
            "source_stopped_attested": False,
            "rollback_boot_verified": False,
            "credential_compatibility_verified": False,
        }
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        print(json.dumps({"status":"BLOCKED","release_authorized":False}))
        return 4
    try:
        result = drill(args.source,args.out)
    except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
        print(json.dumps({"status":"BLOCKED","reason":type(exc).__name__,
                          "release_authorized":False}))
        return 3
    print(json.dumps(result,indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
