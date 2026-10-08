"""Durable, SQLite-backed maintenance marker (foundation only).

The marker persists through API restarts and is readable by different processes.
No production routes or task creators invoke this module yet. A status check
alone is NOT an atomic admission barrier for concurrent task creation.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

KEY = "oci_nt_release_maintenance_v1"


def _connect(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        raise FileNotFoundError(f"Database not found: {db}")
    connection = sqlite3.connect(
        f"{db.resolve().as_uri()}?mode=rw", uri=True, timeout=30, isolation_level=None
    )
    connection.execute("PRAGMA busy_timeout=30000")
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_settings'"
    ).fetchone():
        connection.close()
        raise RuntimeError("system_settings table missing")
    return connection


def read_state(db: Path) -> dict:
    con = _connect(db)
    try:
        row = con.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=?", (KEY,)
        ).fetchone()
        if not row or not row[0]:
            return {"active": False}
        parsed = json.loads(row[0])
        if not isinstance(parsed, dict) or parsed.get("active") is not True:
            if not isinstance(parsed, dict) or parsed.get("active") is not False:
                raise RuntimeError("Maintenance marker malformed; fail closed")
        return parsed
    finally:
        con.close()


def begin(db: Path, *, operator: str, reason: str) -> dict:
    if not operator.strip() or not reason.strip():
        raise ValueError("Operator and reason are required")
    con = _connect(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=?", (KEY,)
        ).fetchone()
        if row and row[0]:
            old = json.loads(row[0])
            if not isinstance(old, dict) or old.get("active") is not False:
                raise RuntimeError("Maintenance already active or marker malformed")
        # Cross-process request leases must be fully drained before maintenance.
        # This is checked under the same SQLite write transaction as activation.
        lease_row = con.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=?",
            ("oci_nt_http_operation_leases_v1",),
        ).fetchone()
        if lease_row:
            try:
                leases = json.loads(lease_row[0])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("OCI operation leases malformed") from exc
            if not isinstance(leases, dict):
                raise RuntimeError("OCI operation leases malformed")
            if leases:
                raise RuntimeError(f"OCI HTTP operations still active: {len(leases)}")
        # The same BEGIN IMMEDIATE transaction serializes this drain check
        # against repository task creation (also BEGIN IMMEDIATE).
        for table in ("manual_tasks", "launch_jobs"):
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                raise RuntimeError(f"Required task table missing: {table}")
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if "status" not in columns:
                raise RuntimeError(f"Task status missing: {table}")
            active = con.execute(
                f"""SELECT COUNT(*) FROM {table}
                WHERE status IS NULL OR UPPER(status) NOT IN
                ('COMPLETED','PARTIAL','FAILED','CANCELLED','INTERRUPTED',
                 'SUCCESS','FINISHED','STOPPED')"""
            ).fetchone()[0]
            if active:
                raise RuntimeError(f"Undrained {table} tasks: {active}")
        state = {
            "active": True,
            "operator": operator.strip(),
            "reason": reason.strip(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        con.execute(
            """INSERT INTO system_settings(setting_key,setting_value,is_secret,updated_at)
            VALUES(?,?,0,CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
              setting_value=excluded.setting_value, updated_at=CURRENT_TIMESTAMP""",
            (KEY, json.dumps(state, ensure_ascii=False)),
        )
        con.execute("COMMIT")
        return state
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def end(db: Path, *, operator: str) -> dict:
    if not operator.strip():
        raise ValueError("Operator required")
    con = _connect(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=?", (KEY,)
        ).fetchone()
        if not row or not row[0]:
            raise RuntimeError("No maintenance marker to close")
        old = json.loads(row[0])
        if not isinstance(old, dict) or old.get("active") is not True:
            raise RuntimeError("Maintenance is not active; refusing to clear marker")
        # Reopening OCI admission must not race with unfinished requests.
        # Check the persistent leases and task rows under the same write lock
        # that clears the maintenance marker.
        lease_row = con.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=?",
            ("oci_nt_http_operation_leases_v1",),
        ).fetchone()
        try:
            leases = json.loads(lease_row[0]) if lease_row else {}
        except (TypeError, ValueError) as exc:
            raise RuntimeError("OCI operation leases malformed") from exc
        if not isinstance(leases, dict):
            raise RuntimeError("OCI operation leases malformed")
        for token, entry in leases.items():
            if (not isinstance(token, str) or not token
                or not isinstance(entry, dict)
                or not isinstance(entry.get("started_at"), str)
                or not entry["started_at"]
                or not isinstance(entry.get("path"), str)
                or not entry["path"]):
                raise RuntimeError("OCI operation lease entry malformed")
        if leases:
            raise RuntimeError(f"Outstanding OCI leases prevent maintenance exit: {len(leases)}")
        for table in ("manual_tasks", "launch_jobs"):
            if not con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                raise RuntimeError(f"Required task table missing: {table}")
            if "status" not in {row[1] for row in con.execute(f"PRAGMA table_info({table})")}:
                raise RuntimeError(f"Task status missing: {table}")
            count = con.execute(
                f"""SELECT COUNT(*) FROM {table}
                WHERE status IS NULL OR UPPER(status) NOT IN
                ('COMPLETED','PARTIAL','FAILED','CANCELLED','INTERRUPTED',
                 'SUCCESS','FINISHED','STOPPED')"""
            ).fetchone()[0]
            if count:
                raise RuntimeError(f"Undrained {table} tasks prevent maintenance exit: {count}")
        state = {
            **old, "active": False, "ended_by": operator.strip(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }
        con.execute(
            "UPDATE system_settings SET setting_value=?,updated_at=CURRENT_TIMESTAMP WHERE setting_key=?",
            (json.dumps(state, ensure_ascii=False), KEY),
        )
        con.execute("COMMIT")
        return state
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
