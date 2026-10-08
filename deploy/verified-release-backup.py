#!/usr/bin/env python3
"""Create a verified, consistent SQLite release snapshot without stopping the API.

This is only a backup tool. It does not enable maintenance mode or authorize a
backend restart. Backups are kept outside the app's automatic retention policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(db: Path, output: Path) -> dict:
    if not db.is_file():
        raise RuntimeError("Source database does not exist")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output, 0o700)
    if not output.is_dir():
        raise RuntimeError("Invalid output directory")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final = output / f"oci-nt-release-{stamp}.db"
    temp = output / f".oci-nt-release-{stamp}.tmp"
    if final.exists() or temp.exists():
        raise RuntimeError("Backup destination already exists")
    connection = None
    backup = None
    try:
        connection = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        backup = sqlite3.connect(str(temp), timeout=30)
        os.chmod(temp, 0o600)
        connection.backup(backup)
        backup.close()
        backup = None
        check = sqlite3.connect(f"{temp.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            quick = check.execute("PRAGMA quick_check").fetchone()[0]
            if quick != "ok":
                raise RuntimeError("Backup integrity check failed")
            schema = check.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()
            if not schema:
                raise RuntimeError("Backup schema version missing")
        finally:
            check.close()
        digest = sha256(temp)
        size = temp.stat().st_size
        temp.replace(final)
        report = {
            "result": "VERIFIED_SNAPSHOT",
            "file": str(final),
            "bytes": size,
            "schema": int(schema[0]),
            "quick_check": quick,
            "sha256": digest,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "note": "Snapshot verified; active task protection and restart authorization are separate gates.",
        }
        manifest = output / (final.name + ".json")
        fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return report
    finally:
        if backup is not None:
            backup.close()
        if connection is not None:
            connection.close()
        temp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(snapshot(args.db, args.output), ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"result": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
