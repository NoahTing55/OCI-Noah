from __future__ import annotations

from app.config import settings
from app.database import database, init_database
from app.task_repository import (
    cancel_pending_items,
    create_task,
    finish_item,
    finish_task,
    cleanup_terminal_tasks,
    get_task,
    request_cancel,
    start_item,
    start_task,
)


def _use_temp_database(tmp_path) -> None:
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "oci-nt.db"
    settings.backup_dir = tmp_path / "backups"
    init_database()


def test_task_progress_partial_and_retry_state(tmp_path):
    _use_temp_database(tmp_path)
    task = create_task(
        task_type="PROXY_HEALTH",
        title="代理检测",
        requested_by="tester",
        items=[
            {"item_key": "1", "item_name": "A", "payload": {"profile_id": 1}},
            {"item_key": "2", "item_name": "B", "payload": {"profile_id": 2}},
        ],
    )
    start_task(task["id"])
    first, second = get_task(task["id"])["items"]
    start_item(first["id"])
    finish_item(first["id"], status="SUCCEEDED", result={"ip_address": "1.1.1.1"})
    start_item(second["id"])
    finish_item(second["id"], status="FAILED", error="timeout")
    result = finish_task(task["id"])

    assert result["status"] == "PARTIAL"
    assert result["completed"] == 2
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["progress_percent"] == 100.0
    assert result["can_retry"] is True


def test_cancelling_pending_items_updates_progress(tmp_path):
    _use_temp_database(tmp_path)
    task = create_task(
        task_type="ACCOUNT_CHECK",
        title="账户检测",
        requested_by="tester",
        items=[{"item_key": str(index), "item_name": str(index)} for index in range(3)],
    )
    start_task(task["id"])
    assert request_cancel(task["id"]) is True
    cancel_pending_items(task["id"])
    result = finish_task(task["id"], status="CANCELLED")

    assert result["completed"] == 3
    assert result["progress_percent"] == 100.0
    assert all(item["status"] == "CANCELLED" for item in result["items"])


def test_interrupted_item_is_retryable_and_cleanup_is_safe(tmp_path):
    _use_temp_database(tmp_path)
    task = create_task(
        task_type="ACCOUNT_CHECK",
        title="重启恢复",
        requested_by="tester",
        items=[{"item_key": "1", "item_name": "租户一"}],
    )
    start_task(task["id"])
    item = get_task(task["id"])["items"][0]
    start_item(item["id"])
    finish_item(item["id"], status="INTERRUPTED", error="service restart")
    result = finish_task(task["id"], status="INTERRUPTED")

    assert result["interrupted"] == 1
    assert result["can_retry"] is True
    assert result["completed"] == 1

    cleanup = cleanup_terminal_tasks(older_than_days=1, keep_latest=100)
    assert cleanup["deleted"] == 0
    assert get_task(task["id"]) is not None


def test_finish_interrupted_marks_all_unfinished_items(tmp_path):
    _use_temp_database(tmp_path)
    task = create_task(
        task_type="PROXY_HEALTH",
        title="中断任务",
        requested_by="tester",
        items=[
            {"item_key": "1", "item_name": "A"},
            {"item_key": "2", "item_name": "B"},
        ],
    )
    start_task(task["id"])
    first = get_task(task["id"])["items"][0]
    start_item(first["id"])
    result = finish_task(task["id"], status="INTERRUPTED", error="shutdown")

    assert result["status"] == "INTERRUPTED"
    assert result["completed"] == 2
    assert result["interrupted"] == 2
    assert result["can_retry"] is True
    assert all(item["status"] == "INTERRUPTED" for item in result["items"])


def test_cleanup_deletes_only_old_terminal_tasks(tmp_path):
    _use_temp_database(tmp_path)
    old_task = create_task(
        task_type="ACCOUNT_CHECK", title="旧任务", requested_by="tester",
        items=[{"item_key": "old", "item_name": "old"}],
    )
    old_item = get_task(old_task["id"])["items"][0]
    start_item(old_item["id"])
    finish_item(old_item["id"], status="SUCCEEDED")
    finish_task(old_task["id"])

    active_task = create_task(
        task_type="ACCOUNT_CHECK", title="运行任务", requested_by="tester",
        items=[{"item_key": "active", "item_name": "active"}],
    )
    start_task(active_task["id"])
    with database() as connection:
        connection.execute(
            "UPDATE manual_tasks SET finished_at = ?, created_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", old_task["id"]),
        )
        connection.execute(
            "UPDATE manual_tasks SET created_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", active_task["id"]),
        )

    result = cleanup_terminal_tasks(older_than_days=1, keep_latest=0)
    assert result["deleted"] == 1
    assert get_task(old_task["id"]) is None
    assert get_task(active_task["id"]) is not None
