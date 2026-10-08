"""Atomic maintenance admission protocol foundation.

Call `admit_task` while creating a durable task row using the *same* SQLite
transaction. Merely invoking it and then creating a job later is NOT safe.

Not integrated with production task repositories yet; no release authorization.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

KEY = "oci_nt_release_maintenance_v1"


class MaintenanceBlocked(RuntimeError):
    pass


@contextmanager
def admitted_transaction(db: Path) -> Iterator[sqlite3.Connection]:
    """Hold SQLite RESERVED write lock through marker check AND task insertion.

    A concurrent maintenance BEGIN IMMEDIATE waits for this transaction;
    once it commits, maintenance can acquire the lock and see the new task.
    When maintenance wins first, admission refuses the new task.
    """
    if not db.is_file():
        raise FileNotFoundError(db)
    con = sqlite3.connect(
        f"{db.resolve().as_uri()}?mode=rw",
        uri=True, timeout=30, isolation_level=None
    )
    try:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("BEGIN IMMEDIATE")
        if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_settings'"
        ).fetchone():
            raise RuntimeError("system_settings missing; refusing admission")
        row = con.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=?",
            (KEY,),
        ).fetchone()
        if row:
            try:
                state = json.loads(row[0])
            except (TypeError, ValueError) as exc:
                raise MaintenanceBlocked("Invalid maintenance marker") from exc
            if not isinstance(state, dict) or type(state.get("active")) is not bool:
                raise MaintenanceBlocked("Invalid maintenance marker")
            if state["active"]:
                raise MaintenanceBlocked("Maintenance is active")
        yield con
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
