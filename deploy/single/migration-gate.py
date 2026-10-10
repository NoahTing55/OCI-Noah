#!/usr/bin/env python3
"""Read-only *advisory* compatibility check for a proposed single-container cutover.

Never authorizes release: runtime OCI leases, external requests, credentials,
concurrent writers and rollback still require independent verification.
"""
import argparse
import json
import sqlite3
from pathlib import Path


def inspect_database(path: Path) -> dict:
    if not path.is_file():
        raise ValueError("database missing")
    if path.is_symlink():
        raise ValueError("symbolic-link database forbidden")
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("integrity check failed")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"schema_version", "manual_tasks", "launch_jobs", "system_settings", "users"}
        if not required.issubset(tables):
            raise ValueError("missing tables: " + ",".join(sorted(required - tables)))
        ver = con.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()
        if not ver or ver[0] != 7:
            raise ValueError("schema version not validated for this candidate")
        active = {}
        terminal = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED", "SUCCESS", "FINISHED", "STOPPED"}
        for table in ("manual_tasks", "launch_jobs"):
            statuses = con.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status").fetchall()
            active[table] = {str(status or "NULL").upper(): number for status, number in statuses
                             if str(status or "NULL").upper() not in terminal}
        return {
            "schema": 7, "quick_check": "ok",
            "nonterminal_tasks": active,
            "task_rows_terminal": not any(active.values()),
            "release_authorized": False,
            "single_container_ready": False,
            "note": "Data shape only; not a live OCI request drain, decryption check, rollback, or production authorization",
        }
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect_database(args.db)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["task_rows_terminal"] else 3
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"single_container_ready": False, "release_authorized": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
