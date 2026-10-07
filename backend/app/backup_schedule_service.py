from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from .config import settings
from .backup_service import create_sqlite_backup
from .database import add_audit_log, database
from .settings_repository import (
    BACKUP_SCHEDULE_LAST_BACKUP_KEY,
    BACKUP_SCHEDULE_LAST_ERROR_KEY,
    BACKUP_SCHEDULE_LAST_RUN_AT_KEY,
    BACKUP_SCHEDULE_LAST_SLOT_KEY,
    BACKUP_SCHEDULE_LAST_STATUS_KEY,
    load_backup_schedule,
    set_setting,
)
from .telegram_service import send_configured_telegram


def _local_now(now_utc: datetime, offset_minutes: int) -> datetime:
    return now_utc + timedelta(minutes=offset_minutes)


def _slot_for(schedule: dict, now_utc: datetime) -> tuple[str, datetime, datetime]:
    offset = int(schedule["timezone_offset_minutes"])
    local_now = _local_now(now_utc, offset)
    hour, minute = [int(part) for part in schedule["local_time"].split(":", 1)]
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule["frequency"] == "WEEKLY":
        weekday = int(schedule["weekday"])
        days_back = (local_now.weekday() - weekday) % 7
        candidate = candidate - timedelta(days=days_back)
        if candidate > local_now:
            candidate -= timedelta(days=7)
        next_local = candidate + timedelta(days=7)
        slot = f"WEEKLY:{candidate.date().isoformat()}:{schedule['local_time']}:{offset}"
    else:
        if candidate > local_now:
            candidate -= timedelta(days=1)
        next_local = candidate + timedelta(days=1)
        slot = f"DAILY:{candidate.date().isoformat()}:{schedule['local_time']}:{offset}"
    due_utc = candidate - timedelta(minutes=offset)
    next_utc = next_local - timedelta(minutes=offset)
    return slot, due_utc.replace(tzinfo=timezone.utc), next_utc.replace(tzinfo=timezone.utc)


def schedule_status(now_utc: datetime | None = None) -> dict:
    now = now_utc or datetime.now(timezone.utc)
    schedule = load_backup_schedule()
    slot, due_at, next_at = _slot_for(schedule, now)
    if schedule.get("last_slot") != slot and now >= due_at:
        computed_next = due_at
    else:
        computed_next = next_at
    return {
        **schedule,
        "due_slot": slot,
        "due_at": due_at.isoformat(),
        "next_run_at": computed_next.isoformat(),
    }


def mark_current_schedule_slot_handled(now_utc: datetime | None = None) -> dict:
    """Start a newly enabled/changed schedule from its next occurrence.

    The most recent slot is marked as handled so enabling a schedule before its
    first future run does not unexpectedly create an immediate catch-up backup.
    """
    status = schedule_status(now_utc)
    set_setting(BACKUP_SCHEDULE_LAST_SLOT_KEY, status["due_slot"])
    return schedule_status(now_utc)


def _claim_slot(slot: str) -> bool:
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = ?",
            (BACKUP_SCHEDULE_LAST_SLOT_KEY,),
        ).fetchone()
        if row and row["setting_value"] == slot:
            return False
        connection.execute(
            """
            INSERT INTO system_settings(setting_key, setting_value, is_secret, updated_at)
            VALUES (?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value=excluded.setting_value, updated_at=CURRENT_TIMESTAMP
            """,
            (BACKUP_SCHEDULE_LAST_SLOT_KEY, slot),
        )
    return True


def run_scheduled_backup_if_due(now_utc: datetime | None = None) -> dict | None:
    now = now_utc or datetime.now(timezone.utc)
    status = schedule_status(now)
    if not status["enabled"] or now < datetime.fromisoformat(status["due_at"]):
        return None
    slot = status["due_slot"]
    if status.get("last_slot") == slot or not _claim_slot(slot):
        return None
    run_at = now.isoformat()
    set_setting(BACKUP_SCHEDULE_LAST_RUN_AT_KEY, run_at)
    try:
        backup = create_sqlite_backup("scheduled")
        set_setting(BACKUP_SCHEDULE_LAST_STATUS_KEY, "SUCCESS")
        set_setting(BACKUP_SCHEDULE_LAST_ERROR_KEY, "")
        set_setting(BACKUP_SCHEDULE_LAST_BACKUP_KEY, backup["name"])
        add_audit_log(
            "system", "SYSTEM_SCHEDULED_BACKUP_CREATED", None,
            f"slot={slot},backup={backup['name']}", "SYSTEM_BACKUP", backup["name"],
        )
        send_configured_telegram(
            f"OCI-N&T 自动本地备份完成\n\n系统版本：{settings.display_version}\n备份文件：" + backup["name"],
            "system_backup",
        )
        return {"ok": True, "slot": slot, "backup": backup}
    except Exception as exc:  # noqa: BLE001 - persisted for the administrator.
        error = f"{type(exc).__name__}: {exc}"[:4000]
        set_setting(BACKUP_SCHEDULE_LAST_STATUS_KEY, "FAILED")
        set_setting(BACKUP_SCHEDULE_LAST_ERROR_KEY, error)
        add_audit_log(
            "system", "SYSTEM_SCHEDULED_BACKUP_FAILED", None,
            f"slot={slot},{error}", "SYSTEM_BACKUP",
        )
        send_configured_telegram(
            f"OCI-N&T 自动本地备份失败\n\n系统版本：{settings.display_version}\n失败原因：" + error,
            "system_backup",
        )
        return {"ok": False, "slot": slot, "error": error}


async def backup_scheduler_loop(stop_event: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=5)
        return
    except asyncio.TimeoutError:
        pass
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run_scheduled_backup_if_due)
        except Exception as exc:  # noqa: BLE001 - keep the scheduler alive.
            error = f"{type(exc).__name__}: {exc}"[:4000]
            set_setting(BACKUP_SCHEDULE_LAST_STATUS_KEY, "FAILED")
            set_setting(BACKUP_SCHEDULE_LAST_ERROR_KEY, error)
            add_audit_log(
                "system", "SYSTEM_BACKUP_SCHEDULER_ERROR", None,
                error, "SYSTEM_BACKUP",
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            continue
