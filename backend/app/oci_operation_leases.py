"""Cross-process SQLite OCI HTTP operation leases.

An operation acquires a durable lease under BEGIN IMMEDIATE, checking the
maintenance marker in the same transaction. The release coordinator must
check outstanding leases while acquiring maintenance under BEGIN IMMEDIATE.
Crash-left leases intentionally fail closed until reviewed by an operator.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MARKER = "oci_nt_release_maintenance_v1"
LEASES = "oci_nt_http_operation_leases_v1"


class OCIAdmissionBlocked(RuntimeError):
    pass


def _decode(value, *, default):
    if value is None:
        return default
    try:
        obj = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise OCIAdmissionBlocked("维护状态损坏，拒绝 OCI 操作") from exc
    if not isinstance(obj, dict):
        raise OCIAdmissionBlocked("维护状态格式异常，拒绝 OCI 操作")
    return obj


def _state(con, key):
    row = con.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key=?", (key,)
    ).fetchone()
    return _decode(row[0] if row else None, default={})


def _open(db: Path):
    if not db.is_file():
        raise OCIAdmissionBlocked("数据库不存在")
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=rw", uri=True, timeout=30, isolation_level=None)
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _write(con, key, obj):
    con.execute(
        """INSERT INTO system_settings(setting_key,setting_value,is_secret,updated_at)
        VALUES(?,?,0,CURRENT_TIMESTAMP)
        ON CONFLICT(setting_key) DO UPDATE SET
          setting_value=excluded.setting_value,updated_at=CURRENT_TIMESTAMP""",
        (key, json.dumps(obj, ensure_ascii=False, separators=(",", ":"))),
    )


def acquire(db: Path, path: str) -> str:
    token = str(uuid.uuid4())
    con = _open(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        marker = _state(con, MARKER)
        if marker and type(marker.get("active")) is not bool:
            raise OCIAdmissionBlocked("维护状态无效")
        if marker.get("active") is True:
            raise OCIAdmissionBlocked("维护模式已开启，拒绝新的 OCI 操作")
        leases = _state(con, LEASES)
        if len(leases) >= 10000:
            raise OCIAdmissionBlocked("未结束 OCI 操作记录超限")
        leases[token] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "path": path[:240],
        }
        _write(con, LEASES, leases)
        con.execute("COMMIT")
        return token
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def release(db: Path, token: str) -> None:
    con = _open(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        leases = _state(con, LEASES)
        if token not in leases:
            raise RuntimeError("OCI operation lease missing; manual investigation required")
        leases.pop(token)
        _write(con, LEASES, leases)
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def active_lease_count_in_transaction(con: sqlite3.Connection) -> int:
    """Called by maintenance begin AFTER acquiring BEGIN IMMEDIATE."""
    leases = _state(con, LEASES)
    return len(leases)


@contextmanager
def admitted_oci_operation(db: Path, path: str):
    token = acquire(db, path)
    try:
        yield
    finally:
        release(db, token)
