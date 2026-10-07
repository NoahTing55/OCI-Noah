from __future__ import annotations

import json
import sqlite3
import threading
import tempfile
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .backup_restore_service import run_backup_restore_drill
from .backup_service import create_sqlite_backup, resolve_backup_path, verify_sqlite_backup
from .config import settings
from .database import database, database_maintenance_lock
from .maintenance_state import begin as begin_maintenance, end as end_maintenance
from .schema_migrations import run_schema_migrations

_restore_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_database(source_path, destination_path) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=60)
    destination = sqlite3.connect(destination_path, timeout=60)
    try:
        source.backup(destination, pages=256, sleep=0.02)
        row = destination.execute("PRAGMA quick_check").fetchone()
        quick = str(row[0] if row else "unknown")
        if quick.lower() != "ok":
            raise RuntimeError(f"数据库复制后 quick_check 失败：{quick}")
    finally:
        destination.close()
        source.close()


def _record_restore(
    *,
    backup_name: str,
    protection_backup: str | None,
    status: str,
    requested_by: str,
    schema_before: int | None,
    schema_after: int | None,
    duration_ms: int,
    error: str | None,
    detail: dict,
) -> dict:
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO database_restore_history(
                backup_name, protection_backup_name, status, requested_by,
                schema_before, schema_after, duration_ms, error, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                backup_name,
                protection_backup,
                status,
                requested_by,
                schema_before,
                schema_after,
                int(duration_ms),
                error,
                json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                _now(),
            ),
        )
        restore_id = int(cursor.lastrowid)
    return {
        "id": restore_id,
        "backup_name": backup_name,
        "protection_backup_name": protection_backup,
        "status": status,
        "requested_by": requested_by,
        "schema_before": schema_before,
        "schema_after": schema_after,
        "duration_ms": duration_ms,
        "error": error,
        "detail": detail,
        "created_at": _now(),
    }


def _schema_version(path) -> int:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        row = connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id = 1"
        ).fetchone()
        return int(row[0] if row else 0)
    except sqlite3.Error:
        return 0
    finally:
        connection.close()


def _active_task_count() -> int:
    with database() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM manual_tasks WHERE status IN ('PENDING','RUNNING','WAITING','CANCELLING')"
        ).fetchone()
        launch = connection.execute(
            "SELECT COUNT(*) FROM launch_jobs WHERE status IN ('PENDING','RUNNING','WAITING','CANCELLING')"
        ).fetchone()
    return int(row[0] if row else 0) + int(launch[0] if launch else 0)


def restore_database_backup(
    backup_name: str,
    *,
    requested_by: str,
    expected_confirmation: str,
) -> dict:
    expected = f"RESTORE {backup_name}"
    if str(expected_confirmation or "").strip() != expected:
        raise ValueError(f"确认文字不正确，请输入：{expected}")
    if not _restore_lock.acquire(blocking=False):
        raise RuntimeError("已有数据库恢复正在执行")
    if not begin_maintenance("DATABASE_RESTORE", backup_name):
        _restore_lock.release()
        raise RuntimeError("系统正在执行其他维护操作")

    started = time.monotonic()
    protection = None
    schema_before = _schema_version(settings.db_path)
    target = resolve_backup_path(backup_name)
    detail: dict = {"confirmation": expected, "target": backup_name}
    temp_source = None
    current_admin = None
    try:
        with database() as connection:
            row = connection.execute(
                """
                SELECT username, password_hash, role, is_active
                FROM users
                WHERE is_active = 1
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()
            current_admin = dict(row) if row else None
        if not current_admin:
            raise RuntimeError("当前数据库没有可用的本地管理员，拒绝恢复")
        if _active_task_count():
            raise RuntimeError("存在运行中的任务，必须等待任务结束后才能恢复数据库")
        verification = verify_sqlite_backup(backup_name)
        detail["verification"] = verification
        if not verification.get("ok"):
            raise RuntimeError(
                f"备份完整性检查失败：{verification.get('quick_check')}"
            )

        drill = run_backup_restore_drill(backup_name)
        detail["restore_drill"] = {
            "id": drill.get("id"),
            "status": drill.get("status"),
            "schema_after": drill.get("schema_after"),
            "openapi_paths": drill.get("openapi_paths"),
            "error": drill.get("error"),
        }
        if drill.get("status") != "SUCCESS":
            raise RuntimeError("恢复演练未通过，拒绝正式恢复")

        # Copy the selected backup before creating the protection backup because
        # retention cleanup must never remove the restore source mid-operation.
        fd, temp_name = tempfile.mkstemp(prefix="oci-nt-restore-source-", suffix=".db", dir=settings.data_dir)
        temp_source = temp_name
        import os
        os.close(fd)
        shutil.copy2(target, temp_source)

        protection_info = create_sqlite_backup("pre-restore")
        protection = protection_info["name"]
        detail["protection_backup"] = protection_info

        # Block all repository connections while replacing and migrating SQLite.
        with database_maintenance_lock:
            _copy_database(temp_source, settings.db_path)
            connection = sqlite3.connect(settings.db_path, timeout=60)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                schema_after = run_schema_migrations(connection)
                restored_admin = connection.execute(
                    "SELECT id FROM users ORDER BY id LIMIT 1"
                ).fetchone()
                if restored_admin:
                    connection.execute(
                        """
                        UPDATE users
                        SET username = ?, password_hash = ?, role = ?, is_active = ?,
                            token_version = token_version + 1, failed_login_count = 0,
                            locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            current_admin["username"], current_admin["password_hash"],
                            current_admin.get("role") or "ADMIN", int(current_admin.get("is_active") or 1),
                            int(restored_admin[0]),
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO users(
                            username, password_hash, role, is_active, token_version,
                            failed_login_count, locked_until, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 1, 0, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """,
                        (
                            current_admin["username"], current_admin["password_hash"],
                            current_admin.get("role") or "ADMIN", int(current_admin.get("is_active") or 1),
                        ),
                    )
                connection.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = 'DATABASE_RESTORE'
                    WHERE revoked_at IS NULL
                    """,
                    (_now(),),
                )
                row = connection.execute("PRAGMA quick_check").fetchone()
                quick = str(row[0] if row else "unknown")
                if quick.lower() != "ok":
                    raise RuntimeError(f"恢复后 quick_check 失败：{quick}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

        duration = int((time.monotonic() - started) * 1000)
        detail["quick_check_after"] = "ok"
        detail["sessions_invalidated"] = True
        detail["current_admin_credentials_preserved"] = True
        return _record_restore(
            backup_name=backup_name,
            protection_backup=protection,
            status="SUCCESS",
            requested_by=requested_by,
            schema_before=schema_before,
            schema_after=schema_after,
            duration_ms=duration,
            error=None,
            detail=detail,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        detail["failure"] = error
        if protection:
            try:
                protection_path = resolve_backup_path(protection)
                with database_maintenance_lock:
                    _copy_database(protection_path, settings.db_path)
                detail["automatic_rollback"] = "SUCCESS"
            except Exception as rollback_exc:
                detail["automatic_rollback"] = f"FAILED: {type(rollback_exc).__name__}: {rollback_exc}"
        duration = int((time.monotonic() - started) * 1000)
        try:
            _record_restore(
                backup_name=backup_name,
                protection_backup=protection,
                status="FAILED",
                requested_by=requested_by,
                schema_before=schema_before,
                schema_after=_schema_version(settings.db_path),
                duration_ms=duration,
                error=error,
                detail=detail,
            )
        except Exception:
            pass
        raise RuntimeError(error) from exc
    finally:
        if temp_source:
            try:
                Path(temp_source).unlink(missing_ok=True)
            except Exception:
                pass
        end_maintenance()
        _restore_lock.release()


def list_database_restores(limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with database() as connection:
        rows = connection.execute(
            "SELECT * FROM database_restore_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["detail"] = json.loads(item.pop("detail_json") or "{}")
        except Exception:
            item["detail"] = {}
        result.append(item)
    return result
