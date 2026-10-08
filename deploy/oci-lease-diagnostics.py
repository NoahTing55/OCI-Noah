#!/usr/bin/env python3
"""Read-only inspection of potentially stranded OCI operation leases.

Never deletes, expires, unlocks, or authorizes release. Fail closed on
malformed data. Timestamps are informational, not proof of liveness.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

KEY = "oci_nt_http_operation_leases_v1"


def inspect(db: Path) -> dict:
    if not db.is_file():
        raise FileNotFoundError(f"Database not found: {db}")
    with sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=10) as con:
        con.execute("PRAGMA query_only=ON")
        row = con.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=?", (KEY,)
        ).fetchone()
    try:
        data = json.loads(row[0]) if row else {}
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Malformed OCI lease record") from exc
    if not isinstance(data, dict):
        raise RuntimeError("OCI lease record must be an object")
    entries = []
    for token, record in data.items():
        if not isinstance(token, str) or not isinstance(record, dict):
            raise RuntimeError("Malformed OCI lease entry")
        started = record.get("started_at")
        path = record.get("path")
        if not isinstance(started, str) or not isinstance(path, str):
            raise RuntimeError("Incomplete OCI lease entry")
        # Never reveal tokens: a token is the identifier used to release a lease.
        entries.append({"started_at": started, "kind": "background" if path.startswith("background:") else "http"})
    return {
        "status": "OUTSTANDING_LEASES" if entries else "NO_RECORDED_LEASES",
        "lease_count": len(entries),
        "entries": entries,
        "absence_proves_quiescence": False,
        "automatic_cleanup_allowed": False,
        "release_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = inspect(args.db)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(json.dumps({"status": "BLOCKED", "release_authorized": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 3 if output["lease_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
