from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .config import settings

REQUIRED_TABLES = {"users", "system_settings", "oci_accounts", "manual_tasks"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backup_path(name: str) -> Path:
    value = str(name or "").strip()
    if (
        not value
        or Path(value).name != value
        or not value.startswith("oci-nt-")
        or not value.endswith(".db")
    ):
        raise ValueError("无效的备份文件名")
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    root = settings.backup_dir.resolve()
    target = settings.backup_dir / value
    resolved = target.resolve(strict=False)
    if resolved.parent != root:
        raise ValueError("无效的备份文件路径")
    if target.is_symlink():
        raise ValueError("不允许验证符号链接备份")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(value)
    return target


def verify_sqlite_backup(name: str) -> dict:
    target = _backup_path(name)
    uri = "file:" + quote(str(target.resolve())) + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        quick_messages = [
            str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
        quick_ok = quick_messages == ["ok"]
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(REQUIRED_TABLES - tables)
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        foreign_key_ok = not foreign_key_rows
    finally:
        connection.close()

    return {
        "ok": bool(quick_ok and not missing and foreign_key_ok),
        "name": target.name,
        "size": target.stat().st_size,
        "verified_at": _utc_now(),
        "quick_check": quick_messages,
        "required_tables_ok": not missing,
        "missing_tables": missing,
        "foreign_key_ok": foreign_key_ok,
        "foreign_key_errors": len(foreign_key_rows),
        "user_version": user_version,
    }
