#!/usr/bin/env python3
"""Independent, fail-closed single-container readiness evidence collector.

Does not restart services, access OCI, or authorize a production cutover.
Current live database is only read via SQLite mode=ro; a separate verified
snapshot is checked without modifying its contents.
"""
import argparse
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load_script(name):
    path = HERE / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit(live_db, snapshot, expected_sha):
    if live_db.is_symlink() or snapshot.is_symlink():
        raise ValueError("symlink is not permitted")
    if not live_db.is_file() or not snapshot.is_file():
        raise ValueError("required database file not found")
    if live_db.resolve() == snapshot.resolve():
        raise ValueError("snapshot must not be the live database")
    if len(expected_sha) != 64 or any(c not in "0123456789abcdef" for c in expected_sha):
        raise ValueError("explicit lowercase SHA-256 required")
    if sha256(snapshot) != expected_sha:
        raise ValueError("snapshot checksum mismatch")

    migration = load_script("migration-gate.py")
    drill = load_script("offline-cutover-drill.py")
    drain = load_script("../release-drain-check.py")

    evidence = {
        "live_database": migration.inspect_database(live_db),
        "drain": drain.report(live_db),
        "snapshot": migration.inspect_database(snapshot),
        "offline_rehearsal": drill.rehearse(snapshot, expected_sha),
    }
    if sha256(snapshot) != expected_sha:
        raise ValueError("snapshot changed during inspection")
    same_schema = evidence["live_database"]["schema"] == evidence["snapshot"]["schema"]
    drained = evidence["drain"]["db_task_drain_ready"]
    terminal = evidence["live_database"]["task_rows_terminal"]
    # This is evidence only: untracked in-flight OCI work, ingress admission,
    # secret decryption, host isolation and real image rollback remain unverified.
    return {
        "result": "READ_ONLY_EVIDENCE_COLLECTED",
        "schema_compatible": same_schema,
        "persisted_drain_indicates_ready": drained and terminal,
        "evidence": evidence,
        "outstanding_gates": [
            "authenticated atomic OCI admission lock and cross-process drain",
            "sanitized production snapshot startup without OCI network egress",
            "Docker socket boundary threat assessment",
            "isolated forward and reverse container image drill",
            "human-approved production window and rollback",
        ],
        "production_switch_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-db", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        result = audit(args.live_db, args.snapshot, args.sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["schema_compatible"] and result["persisted_drain_indicates_ready"] else 3
    except (OSError, ValueError, RuntimeError, sqlite3.Error, AssertionError) as exc:
        print(json.dumps({"result": "BLOCKED", "production_switch_authorized": False, "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
