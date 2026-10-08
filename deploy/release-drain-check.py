#!/usr/bin/env python3
"""Read-only release drain readiness report; NOT a backend restart authorization."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

KEY = "oci_nt_release_maintenance_v1"
LEASE_KEY = "oci_nt_http_operation_leases_v1"
TERMINAL = frozenset(("COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED", "SUCCESS", "FINISHED", "STOPPED"))
TABLES = ("manual_tasks", "launch_jobs")


def report(db: Path) -> dict:
    if not db.is_file():
        raise RuntimeError("Missing database")
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        try:
            integrity = con.execute("PRAGMA quick_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError("SQLite quick_check failed")
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"system_settings", *TABLES}.issubset(names):
                raise RuntimeError("Required task/maintenance table missing")
            state_row = con.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key=?", (KEY,)
            ).fetchone()
            if state_row is None:
                state = {"active": False}
            else:
                try:
                    state = json.loads(state_row[0])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("Malformed maintenance state") from exc
                if not isinstance(state, dict) or type(state.get("active")) is not bool:
                    raise RuntimeError("Malformed maintenance state")
            counts = {}
            for table in TABLES:
                rows = con.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status").fetchall()
                counts[table] = {
                    str(status).upper() if status is not None else "NULL": count
                    for status, count in rows
                    if status is None or str(status).upper() not in TERMINAL
                }
            lease_row = con.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key=?", (LEASE_KEY,)
            ).fetchone()
            try:
                leases = json.loads(lease_row[0]) if lease_row else {}
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Malformed OCI operation leases") from exc
            if not isinstance(leases, dict):
                raise RuntimeError("Malformed OCI operation leases")
            for lease_id, entry in leases.items():
                if not isinstance(lease_id, str) or not isinstance(entry, dict):
                    raise RuntimeError("Malformed OCI operation lease entry")
            gate = state["active"] is True
            empty = all(not count for count in counts.values())
            no_leases = not leases
            return {
                "maintenance_active": gate,
                "nonterminal_tasks": counts,
                "database_quick_check": "ok",
                "oci_operation_leases": len(leases),
                "db_task_drain_ready": gate and empty and no_leases,
                "release_authorized": False,
                "limitations": "Only persisted leases are counted; untracked and detached OCI requests across other processes may still be active.",
            }
        finally:
            con.execute("ROLLBACK")
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = report(args.db)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"drain": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    result["drain"] = "DB_TASKS_DRAINED" if result["db_task_drain_ready"] else "BLOCKED"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["db_task_drain_ready"] else 3


if __name__ == "__main__":
    sys.exit(main())
