from __future__ import annotations

import asyncio
import sqlite3
import sys
import types
from pathlib import Path

import pytest

sys.modules.setdefault("oci", types.ModuleType("oci"))

from app.auth_session_service import (
    create_login_session,
    record_login_failure,
    revoke_session,
    user_lock_status,
    validate_access_token,
)
from app.backup_service import create_sqlite_backup
from app.config import settings
from app.database import database, ensure_admin_user, init_database
from app.database_restore_service import list_database_restores, restore_database_backup
from app.system_resource_service import assert_task_resources
from app.task_recovery_service import preview_safe_resume
from app.task_repository import create_task


def _use_temp_database(tmp_path: Path) -> None:
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "oci-nt.db"
    settings.backup_dir = tmp_path / "backups"
    init_database()


def test_schema_five_contains_sessions_restore_and_recovery_columns(tmp_path):
    _use_temp_database(tmp_path)
    with sqlite3.connect(settings.db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        user_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)")
        }
        item_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(manual_task_items)")
        }
        version = connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id = 1"
        ).fetchone()[0]
        migrations = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert version == 7
    assert [row[0] for row in migrations] == [1, 2, 3, 4, 5, 6, 7]
    assert {"auth_sessions", "login_attempts", "database_restore_history"} <= tables
    assert {"token_version", "failed_login_count", "locked_until"} <= user_columns
    assert {"recovery_state", "recovery_note", "observed_state"} <= item_columns


def test_login_sessions_can_be_revoked_and_failures_lock_account(tmp_path):
    _use_temp_database(tmp_path)
    ensure_admin_user("Noah", "x")

    token, session = create_login_session(
        username="Noah",
        ip_address="203.0.113.10",
        user_agent="pytest",
        login_method="LOCAL",
    )
    validated = validate_access_token(token, touch=False)
    assert validated is not None
    assert validated[0]["username"] == "Noah"
    assert validated[1]["session_id"] == session["session_id"]

    assert revoke_session(session["session_id"], validated[0]["id"])
    assert validate_access_token(token, touch=False) is None

    for _ in range(5):
        record_login_failure(
            "Noah", "203.0.113.10", "pytest", "INVALID_CREDENTIALS"
        )
    lock = user_lock_status("Noah")
    assert lock["locked"] is True
    assert lock["failed_login_count"] >= 5
    assert lock["locked_until"]


def test_formal_restore_uses_protection_backup_and_invalidates_sessions(tmp_path, monkeypatch):
    _use_temp_database(tmp_path)
    ensure_admin_user("Noah", "x")
    token, _session = create_login_session(
        username="Noah",
        ip_address="203.0.113.10",
        user_agent="pytest",
        login_method="LOCAL",
    )
    with database() as connection:
        password_hash_before = connection.execute(
            "SELECT password_hash FROM users WHERE username = 'Noah'"
        ).fetchone()[0]

    with database() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO system_settings(setting_key, setting_value, updated_at) VALUES ('restore_marker', 'before', CURRENT_TIMESTAMP)"
        )
    backup = create_sqlite_backup("formal-restore-test")
    with database() as connection:
        connection.execute(
            "UPDATE system_settings SET setting_value = 'after', updated_at = CURRENT_TIMESTAMP WHERE setting_key = 'restore_marker'"
        )

    monkeypatch.setattr(
        "app.database_restore_service.run_backup_restore_drill",
        lambda name: {
            "id": 1,
            "status": "SUCCESS",
            "schema_after": 5,
            "openapi_paths": 150,
            "error": None,
        },
    )

    result = restore_database_backup(
        backup["name"],
        requested_by="Noah",
        expected_confirmation=f"RESTORE {backup['name']}",
    )

    with database() as connection:
        marker = connection.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = 'restore_marker'"
        ).fetchone()[0]
        user_row = connection.execute(
            "SELECT password_hash, token_version FROM users WHERE username = 'Noah'"
        ).fetchone()
        password_hash_after, token_version = user_row

    assert result["status"] == "SUCCESS"
    assert result["protection_backup_name"].startswith("oci-nt-pre-restore-")
    assert marker == "before"
    assert password_hash_after == password_hash_before
    assert token_version >= 1
    assert result["detail"]["current_admin_credentials_preserved"] is True
    assert validate_access_token(token, touch=False) is None
    history = list_database_restores()
    assert history and history[0]["backup_name"] == backup["name"]


def test_interrupted_instance_preview_checks_live_state_and_blocks_ip_replace(tmp_path, monkeypatch):
    _use_temp_database(tmp_path)
    item = {
        "item_key": "1:instance-a",
        "item_name": "instance-a",
        "item_type": "OCI_INSTANCE",
        "payload": {
            "account_id": 1,
            "instance_id": "ocid1.instance.test",
            "region": "uk-london-1",
        },
    }
    start_task = create_task(
        task_type="INSTANCE_BATCH",
        title="批量开机",
        requested_by="Noah",
        items=[item],
        options={"operation": "START", "interval_seconds": 2},
    )
    with database() as connection:
        connection.execute(
            "UPDATE manual_tasks SET status='INTERRUPTED', interrupted=1, completed=1 WHERE id=?",
            (start_task["id"],),
        )
        connection.execute(
            "UPDATE manual_task_items SET status='INTERRUPTED', recovery_state='STATE_CHECK_REQUIRED' WHERE task_id=?",
            (start_task["id"],),
        )

    monkeypatch.setattr(
        "app.task_recovery_service.get_instance_live",
        lambda *_args, **_kwargs: {"lifecycle_state": "RUNNING"},
    )
    preview = asyncio.run(preview_safe_resume(start_task["id"]))
    assert preview["safe"] is True
    assert preview["items"][0]["decision"] == "ALREADY_SATISFIED"
    assert preview["items"][0]["live_state"] == "RUNNING"

    replace_task = create_task(
        task_type="INSTANCE_BATCH",
        title="更换公网 IP",
        requested_by="Noah",
        items=[item],
        options={"operation": "REPLACE_PUBLIC_IP", "interval_seconds": 2},
        idempotency_key="replace-ip-test",
    )
    with database() as connection:
        connection.execute(
            "UPDATE manual_tasks SET status='INTERRUPTED', interrupted=1, completed=1 WHERE id=?",
            (replace_task["id"],),
        )
        connection.execute(
            "UPDATE manual_task_items SET status='INTERRUPTED', recovery_state='MANUAL_ONLY' WHERE task_id=?",
            (replace_task["id"],),
        )
    blocked = asyncio.run(preview_safe_resume(replace_task["id"]))
    assert blocked["safe"] is False
    assert blocked["items"][0]["decision"] == "MANUAL_ONLY"


def test_resource_guard_blocks_large_or_low_resource_tasks(monkeypatch):
    status = {
        "active_tasks": 0,
        "memory_available_bytes": 2 * 1024 * 1024 * 1024,
        "swap_free_bytes": 0,
        "disk_free_bytes": 10 * 1024 * 1024 * 1024,
        "limits": {
            "min_free_memory_mb": 384,
            "min_free_disk_mb": 1024,
            "max_active_tasks": 2,
            "max_task_items": 5,
        },
    }
    monkeypatch.setattr(
        "app.system_resource_service.collect_resource_status", lambda: dict(status)
    )
    assert assert_task_resources(5)["active_tasks"] == 0
    with pytest.raises(RuntimeError, match="项目数超过"):
        assert_task_resources(6)

    low_memory = dict(status)
    low_memory["memory_available_bytes"] = 64 * 1024 * 1024
    monkeypatch.setattr(
        "app.system_resource_service.collect_resource_status", lambda: low_memory
    )
    with pytest.raises(RuntimeError, match="可用内存不足"):
        assert_task_resources(1)


def test_v2_atomic_upgrade_contract_is_low_memory_safe_and_schema_seven():
    root = Path(__file__).resolve().parents[2]
    script = (root / "deploy/atomic-upgrade-v2.0.0.sh").read_text(encoding="utf-8")
    assert "--low-memory" in script
    assert "--normal-memory" in script
    assert "LOW_MEMORY_MODE" in script
    assert "docker compose build api" in script
    assert "docker compose build web" in script
    assert "DATABASE_SCHEMA_OK version=7" in script
    assert "migrations[:7] == [1, 2, 3, 4, 5, 6, 7]" in script
    assert "pre-v2-upgrade" in script
