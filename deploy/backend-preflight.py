#!/usr/bin/env python3
"""Fail-closed read-only preflight for OCI-N&T backend releases.

This script never changes the database, images or running containers. A passing
result does NOT authorize a backend restart or a DB migration.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED", "SUCCESS", "FINISHED", "STOPPED"}
TASK_TABLES = ("manual_tasks", "launch_jobs")
REQUIRED = ("manual_tasks", "launch_jobs", "schema_version", "system_upgrade_history")

def inspect(db: Path) -> dict:
    if not db.is_file():
        raise RuntimeError(f"Database missing: {db}")
    uri = f"{db.resolve().as_uri()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        integrity = con.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Database quick_check failed: {integrity}")
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        missing = sorted(set(REQUIRED) - tables)
        if missing:
            raise RuntimeError(f"Required tables missing: {missing}")
        schema = con.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()
        if schema is None:
            raise RuntimeError("Missing schema version")
        pending = {}
        for table in TASK_TABLES:
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if "status" not in columns:
                raise RuntimeError(f"Missing status column: {table}")
            items = {str(status or "NULL").upper(): count for status, count in con.execute(
                f"SELECT status, count(*) FROM {table} GROUP BY status"
            )}
            pending[table] = {k:v for k,v in items.items() if k not in TERMINAL}
        return {"database_quick_check": "ok", "schema": int(schema[0]), "nonterminal_tasks": pending,
                "safe_to_restart": all(not v for v in pending.values())}
    finally:
        con.close()

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, required=True)
    args = p.parse_args()
    try:
        report = inspect(args.db)
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"preflight": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    report["preflight"] = "PASS" if report["safe_to_restart"] else "BLOCKED_ACTIVE_TASKS"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["safe_to_restart"] else 3

if __name__ == "__main__":
    sys.exit(main())
