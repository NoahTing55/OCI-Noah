"""Repository-level durable maintenance admission checks.

Must be called INSIDE a SQLite BEGIN IMMEDIATE transaction, using the SAME
connection that creates a job. A check before acquiring the write lock is unsafe.
"""
from __future__ import annotations

import json
import sqlite3

MAINTENANCE_KEY = "oci_nt_release_maintenance_v1"


class DurableMaintenanceBlocked(RuntimeError):
    pass


def assert_admission_in_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError("Maintenance admission requires an active SQLite transaction")
    # Caller must have already acquired BEGIN IMMEDIATE; Python sqlite3 does not
    # expose transaction lock strength for introspection.
    row = connection.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key = ?",
        (MAINTENANCE_KEY,),
    ).fetchone()
    if row is None:
        return
    try:
        state = json.loads(row[0])
    except (ValueError, TypeError) as exc:
        raise DurableMaintenanceBlocked("维护状态异常，拒绝创建新的 OCI 任务") from exc
    if not isinstance(state, dict) or type(state.get("active")) is not bool:
        raise DurableMaintenanceBlocked("维护状态异常，拒绝创建新的 OCI 任务")
    if state["active"]:
        raise DurableMaintenanceBlocked("系统已进入发布维护模式，暂停创建新的 OCI 任务")
