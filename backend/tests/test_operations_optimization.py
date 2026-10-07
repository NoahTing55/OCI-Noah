from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from app.backup_service import cleanup_old_backups
from app.config import settings
from app.database import init_database
from app.settings_repository import load_backup_policy, save_backup_policy
from app.system_diagnostics import collect_support_bundle
from app.task_repository import (
    create_task,
    find_active_account_conflicts,
    finish_task,
    start_task,
)


def _use_temp_database(tmp_path) -> None:
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "oci-nt.db"
    settings.backup_dir = tmp_path / "backups"
    init_database()


def _create_backup_file(name, modified_at) -> None:
    path = settings.backup_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    timestamp = modified_at.timestamp()
    os.utime(path, (timestamp, timestamp))


def test_backup_policy_and_cleanup_preview_are_deterministic(tmp_path):
    _use_temp_database(tmp_path)
    saved = save_backup_policy(retention_days=7, keep_latest=2, max_count=3)
    assert saved == {"retention_days": 7, "keep_latest": 2, "max_count": 3}
    assert load_backup_policy() == saved

    now = datetime.now(timezone.utc)
    _create_backup_file("oci-nt-test-newest.db", now)
    _create_backup_file("oci-nt-test-second.db", now - timedelta(hours=1))
    _create_backup_file("oci-nt-test-third.db", now - timedelta(days=10))
    _create_backup_file("oci-nt-test-fourth.db", now - timedelta(days=20))

    preview = cleanup_old_backups(dry_run=True)
    assert preview["candidate_count"] == 2
    assert preview["deleted_count"] == 0
    assert preview["remaining_count"] == 4
    assert [item["name"] for item in preview["candidates"]] == [
        "oci-nt-test-third.db",
        "oci-nt-test-fourth.db",
    ]

    result = cleanup_old_backups(dry_run=False)
    assert result["deleted_count"] == 2
    assert result["remaining_count"] == 2
    assert len(list(settings.backup_dir.glob("oci-nt-*.db"))) == 2


def test_active_account_conflict_detection_only_returns_overlaps(tmp_path):
    _use_temp_database(tmp_path)
    task = create_task(
        task_type="ACCOUNT_CHECK",
        title="账户检测",
        requested_by="tester",
        items=[
            {
                "item_key": "10",
                "item_name": "租户十",
                "payload": {"account_id": 10},
            },
            {
                "item_key": "20",
                "item_name": "租户二十",
                "payload": {"account_id": 20},
            },
        ],
    )
    start_task(task["id"])

    conflicts = find_active_account_conflicts([20, 30])
    assert len(conflicts) == 1
    assert conflicts[0]["id"] == task["id"]
    assert conflicts[0]["account_ids"] == [20]
    assert find_active_account_conflicts([30]) == []

    finish_task(task["id"], status="INTERRUPTED", error="test")
    assert find_active_account_conflicts([10, 20]) == []


def test_support_bundle_is_local_and_secret_free(tmp_path):
    _use_temp_database(tmp_path)
    save_backup_policy(retention_days=30, keep_latest=3, max_count=20)
    task = create_task(
        task_type="PROXY_HEALTH",
        title="脱敏测试",
        requested_by="tester",
        items=[{"item_key": "1", "item_name": "proxy"}],
    )
    finish_task(
        task["id"],
        status="FAILED",
        error="socks5://alice:secret@example.com:1080?token=abcdef Bearer abc.def",
    )
    bundle = collect_support_bundle()

    assert bundle["service"] == "OCI-N&T"
    assert bundle["version"] == "2.0.0"
    assert bundle["database"]["schema_version"] == 7
    assert bundle["backups"]["policy"]["keep_latest"] == 3
    assert "credential_encryption_key" not in str(bundle).lower()
    assert "secret_key" not in str(bundle).lower()
    assert "bot_token" not in str(bundle).lower()
    assert "alice" not in str(bundle)
    assert "secret" not in str(bundle)
    assert "abcdef" not in str(bundle)
    assert "abc.def" not in str(bundle)
