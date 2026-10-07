from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.backup_restore_service import run_backup_restore_drill
from app.backup_schedule_service import run_scheduled_backup_if_due, schedule_status
from app.backup_service import create_sqlite_backup
from app.config import settings
from app.database import init_database
from app.oci_resilience import adaptive_oci_call, classify_oci_error
from app.release_service import current_release_info, record_release_event
from app.settings_repository import save_backup_schedule
from app.task_repository import create_task, get_task


def _use_temp_database(tmp_path) -> None:
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "oci-nt.db"
    settings.backup_dir = tmp_path / "backups"
    init_database()


def test_task_creation_is_idempotent(tmp_path):
    _use_temp_database(tmp_path)
    items = [{"item_key": "1", "item_name": "租户", "payload": {"account_id": 1}}]
    first = create_task(
        task_type="ACCOUNT_CHECK",
        title="账户检测",
        requested_by="tester",
        items=items,
        idempotency_key="browser-request-1",
    )
    second = create_task(
        task_type="ACCOUNT_CHECK",
        title="账户检测",
        requested_by="tester",
        items=items,
        idempotency_key="browser-request-1",
    )
    assert first["id"] == second["id"]
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert get_task(first["id"])["idempotency_key"] == "browser-request-1"


def test_oci_error_classification_and_adaptive_retry(monkeypatch):
    class RateLimited(RuntimeError):
        status = 429
        code = "TooManyRequests"

    info = classify_oci_error(RateLimited("rate limit"))
    assert info.category == "RATE_LIMIT"
    assert info.retryable is True

    calls = {"count": 0}

    def operation():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RateLimited("rate limit")
        return "ok"

    monkeypatch.setattr("app.oci_resilience.retry_delay_seconds", lambda *_: 0)
    retries = []
    result, attempts = asyncio.run(
        adaptive_oci_call(
            operation,
            max_attempts=3,
            on_retry=lambda details, attempt, delay, next_at: retries.append(
                (details.category, attempt, delay, next_at)
            ),
        )
    )
    assert result == "ok"
    assert attempts == 2
    assert retries and retries[0][0] == "RATE_LIMIT"


def test_local_backup_schedule_claims_each_slot_once(tmp_path, monkeypatch):
    _use_temp_database(tmp_path)
    save_backup_schedule(
        enabled=True,
        frequency="DAILY",
        local_time="03:00",
        weekday=0,
        timezone_offset_minutes=0,
    )
    created = []
    monkeypatch.setattr(
        "app.backup_schedule_service.create_sqlite_backup",
        lambda reason: created.append(reason) or {"name": "scheduled.db", "ok": True},
    )
    monkeypatch.setattr(
        "app.backup_schedule_service.send_configured_telegram",
        lambda *_args, **_kwargs: None,
    )
    now = datetime(2026, 8, 1, 3, 5, tzinfo=timezone.utc)
    first = run_scheduled_backup_if_due(now)
    second = run_scheduled_backup_if_due(now)
    status = schedule_status(now)

    assert first and first["ok"] is True
    assert second is None
    assert created == ["scheduled"]
    assert status["last_status"] == "SUCCESS"
    assert status["last_backup_name"] == "scheduled.db"


def test_backup_restore_drill_uses_isolated_copy(tmp_path):
    _use_temp_database(tmp_path)
    backup = create_sqlite_backup("restore-drill-test")
    original_size = settings.db_path.stat().st_size
    result = run_backup_restore_drill(backup["name"], timeout_seconds=90)

    assert result["status"] == "SUCCESS"
    assert result["quick_check"] == "ok"
    assert result["schema_after"] == 7
    assert result["app_version"] == "2.0.0"
    assert result["openapi_paths"] >= 1
    assert settings.db_path.stat().st_size == original_size


def test_release_manifest_and_history_are_visible(tmp_path):
    _use_temp_database(tmp_path)
    event = record_release_event(
        event_type="UPGRADE_COMPLETED",
        status="SUCCESS",
        previous_version="1.0.1",
        detail={"message": "test upgrade"},
    )
    info = current_release_info()

    assert info["version"] == "2.0.0"
    assert info["schema_version"] == 7
    assert info["build_id"].startswith("v2.0.0")
    assert info["latest_event"]["id"] == event["id"]
    assert info["latest_event"]["detail"]["message"] == "test upgrade"
