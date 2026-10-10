#!/usr/bin/env python3
"""Offline cutover rehearsal: NEVER starts/stops real services or writes source DB.

Given a verified SQLite snapshot, creates two disposable copies, checks
snapshot integrity, models single-image boot inspection and four-container
rollback data compatibility. This is NOT release authorization.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path


def checksum(p):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inspect(p):
    with sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True) as c:
        c.execute("PRAGMA query_only=ON")
        assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        ver = c.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()
        if not ver or int(ver[0]) != 7:
            raise ValueError("unvalidated database schema")
        return int(ver[0])


def rehearse(snapshot: Path, sha256: str):
    if snapshot.is_symlink() or not snapshot.is_file():
        raise ValueError("snapshot must be a regular file")
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("explicit lowercase SHA-256 required")
    before = checksum(snapshot)
    if before != sha256:
        raise ValueError("snapshot SHA-256 mismatch")
    schema = inspect(snapshot)
    with tempfile.TemporaryDirectory(prefix="oci-single-cutover-drill-") as temp:
        base = Path(temp)
        candidate = base / "candidate.db"
        restored = base / "rollback.db"
        with sqlite3.connect(f"{snapshot.resolve().as_uri()}?mode=ro", uri=True) as source:
            with sqlite3.connect(candidate) as target:
                source.backup(target)
            with sqlite3.connect(restored) as target:
                source.backup(target)
        if inspect(candidate) != schema or inspect(restored) != schema:
            raise RuntimeError("data copy schema mismatch")
        if checksum(candidate) != checksum(restored):
            raise RuntimeError("candidate and rollback copies differ")
    if checksum(snapshot) != before:
        raise RuntimeError("source snapshot changed")
    return {
        "result": "OFFLINE_DATA_REHEARSAL_OK", "schema": schema,
        "source_unchanged": True, "candidate_copy_valid": True,
        "rollback_copy_valid": True, "runtime_task_drain_verified": False,
        "image_rollback_verified": False, "production_switch_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(rehearse(args.snapshot, args.sha256), indent=2))
    except (OSError, ValueError, RuntimeError, sqlite3.Error, AssertionError) as exc:
        print(json.dumps({"result": "BLOCKED", "production_switch_authorized": False, "reason": str(exc)}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
