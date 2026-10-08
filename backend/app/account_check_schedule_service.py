from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

from .account_check_alert_service import (
    get_alert_state_public,
    load_alert_policy,
    normalize_alert_policy,
    save_alert_policy,
)
from .database import add_audit_log, database
from .settings_repository import get_bulk_check_interval_seconds
from .task_repository import get_task
from .task_service import TaskBusyError, bulk_task_view, start_account_check_task

BulkCheckBusyError = TaskBusyError

SETTING_KEY = "account_check_schedule_v1"
FEATURE = "1.0.5-alert-health-taskcenter1"
_scheduler_task: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _parse(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _default_interval() -> int:
    try:
        value = int(get_bulk_check_interval_seconds())
    except Exception:
        value = 30
    return max(0, min(value, 86400))


def _defaults() -> dict:
    return {
        "enabled": False,
        "mode": "interval",
        "interval_hours": 24,
        "daily_time": "02:00",
        "account_interval_seconds": _default_interval(),
        "timezone_offset_minutes": 0,
        "next_run_at": None,
        "last_attempt_at": None,
        "last_job_id": None,
        "last_status": "NEVER",
        "last_message": "尚未执行定时检测",
        "updated_at": None,
    }


def _normalize(data: dict) -> dict:
    src = dict(data or {})
    mode = str(src.get("mode") or "interval").lower()
    if mode not in {"interval", "daily"}:
        raise ValueError("执行方式只能是 interval 或 daily")
    hours = int(src.get("interval_hours", 24))
    if not 1 <= hours <= 720:
        raise ValueError("检测周期必须在 1–720 小时之间")
    daily = str(src.get("daily_time") or "02:00")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily):
        raise ValueError("每天固定时间格式必须为 HH:MM")
    account_interval = int(src.get("account_interval_seconds", _default_interval()))
    if not 0 <= account_interval <= 86400:
        raise ValueError("账户间隔必须在 0–86400 秒之间")
    tz_offset = int(src.get("timezone_offset_minutes", 0))
    if not -720 <= tz_offset <= 840:
        raise ValueError("时区偏移超出允许范围")
    job_id = src.get("last_job_id")
    return {
        "enabled": bool(src.get("enabled", False)),
        "mode": mode,
        "interval_hours": hours,
        "daily_time": daily,
        "account_interval_seconds": account_interval,
        "timezone_offset_minutes": tz_offset,
        "next_run_at": _iso(_parse(src.get("next_run_at"))),
        "last_attempt_at": _iso(_parse(src.get("last_attempt_at"))),
        "last_job_id": int(job_id) if job_id not in (None, "") else None,
        "last_status": str(src.get("last_status") or "NEVER")[:40],
        "last_message": str(src.get("last_message") or "尚未执行定时检测")[:500],
        "updated_at": _iso(_parse(src.get("updated_at"))),
    }


def _read() -> dict:
    data = _defaults()
    with database() as con:
        row = con.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=?", (SETTING_KEY,)
        ).fetchone()
    if row and row["setting_value"]:
        try:
            stored = json.loads(str(row["setting_value"]))
        except Exception:
            stored = {}
        if isinstance(stored, dict):
            data.update(stored)
    return _normalize(data)


def _write(config: dict) -> dict:
    data = _normalize(config)
    data["updated_at"] = _iso(_now())
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with database() as con:
        con.execute(
            """
            INSERT INTO system_settings(setting_key,setting_value,is_secret,updated_at)
            VALUES(?,?,0,CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
              setting_value=excluded.setting_value,is_secret=0,updated_at=CURRENT_TIMESTAMP
            """,
            (SETTING_KEY, payload),
        )
    return data


def _next(config: dict, now: datetime | None = None) -> datetime:
    now = (now or _now()).astimezone(timezone.utc)
    if config["mode"] == "interval":
        return now + timedelta(hours=int(config["interval_hours"]))
    hour, minute = map(int, config["daily_time"].split(":"))
    offset = timedelta(minutes=int(config["timezone_offset_minutes"]))
    local_now = now + offset
    target = (
        datetime(
            local_now.year,
            local_now.month,
            local_now.day,
            hour,
            minute,
            tzinfo=timezone.utc,
        )
        - offset
    )
    if target <= now:
        target += timedelta(days=1)
    return target


def _job(job_id) -> dict | None:
    if not job_id:
        return None
    try:
        item = get_task(int(job_id))
    except Exception:
        return None
    if not item or item.get("task_type") != "ACCOUNT_CHECK":
        return None
    source = str((item.get("options") or {}).get("source") or "").lower()
    requested_by = str(item.get("requested_by") or "")
    if source not in {"scheduled", "schedule_now"} and not (
        requested_by == "系统定时检测" or "定时页立即检测" in requested_by
    ):
        return None
    return bulk_task_view(item)


def get_account_check_schedule() -> dict:
    config = _read()
    if config["enabled"] and not _parse(config.get("next_run_at")):
        config["next_run_at"] = _iso(_next(config))
    last_job = _job(config.get("last_job_id"))
    effective = (
        last_job.get("status")
        if last_job and last_job.get("status")
        else config["last_status"]
    )
    return {
        "feature": FEATURE,
        "scheduler_running": bool(_scheduler_task and not _scheduler_task.done()),
        "settings": {
            k: config[k]
            for k in (
                "enabled",
                "mode",
                "interval_hours",
                "daily_time",
                "account_interval_seconds",
                "timezone_offset_minutes",
            )
        },
        "alert_policy": load_alert_policy(),
        "alert_state": get_alert_state_public(),
        "next_run_at": config.get("next_run_at"),
        "last_attempt_at": config.get("last_attempt_at"),
        "last_job_id": config.get("last_job_id"),
        "last_status": config.get("last_status"),
        "effective_last_status": effective,
        "last_message": config.get("last_message"),
        "last_job": last_job,
        "updated_at": config.get("updated_at"),
    }


def save_account_check_schedule(
    payload: dict,
    *,
    changed_by: str,
    ip_address: str | None,
) -> dict:
    config = _read()
    for key in (
        "enabled",
        "mode",
        "interval_hours",
        "daily_time",
        "account_interval_seconds",
        "timezone_offset_minutes",
    ):
        if key in payload:
            config[key] = payload[key]

    config = _normalize(config)
    requested_policy = payload.get("alert_policy")
    normalized_policy = (
        normalize_alert_policy(requested_policy)
        if requested_policy is not None
        else None
    )
    config["next_run_at"] = _iso(_next(config)) if config["enabled"] else None
    saved = _write(config)
    policy = (
        save_alert_policy(normalized_policy)
        if normalized_policy is not None
        else load_alert_policy()
    )
    add_audit_log(
        changed_by,
        "OCI_ACCOUNT_CHECK_SCHEDULE_UPDATED",
        ip_address,
        (
            f"enabled={int(saved['enabled'])},mode={saved['mode']},"
            f"hours={saved['interval_hours']},daily={saved['daily_time']},"
            f"account_interval={saved['account_interval_seconds']},"
            f"notify_mode={policy['notify_mode']},"
            f"consecutive_failures={policy['consecutive_failures']},"
            f"recovery_notify={int(policy['recovery_notify'])}"
        ),
        "SYSTEM_SETTING",
    )
    return get_account_check_schedule()


async def _launch(
    config: dict,
    *,
    requested_by: str,
    ip_address: str | None,
    scheduled: bool,
) -> dict:
    config["last_attempt_at"] = _iso(_now())
    source = "scheduled" if scheduled else "schedule_now"
    try:
        task = await start_account_check_task(
            requested_by=requested_by,
            ip_address=ip_address,
            interval_seconds=int(config["account_interval_seconds"]),
            source=source,
        )
    except TaskBusyError as exc:
        config["last_status"] = "SKIPPED_BUSY"
        config["last_message"] = str(exc)
        _write(config)
        add_audit_log(
            requested_by,
            "OCI_ACCOUNT_CHECK_SCHEDULE_SKIPPED",
            ip_address,
            str(exc),
            "MANUAL_TASK",
        )
        raise
    except Exception as exc:
        config["last_status"] = "ERROR"
        config["last_message"] = f"{type(exc).__name__}: {exc}"
        _write(config)
        add_audit_log(
            requested_by,
            "OCI_ACCOUNT_CHECK_SCHEDULE_FAILED",
            ip_address,
            config["last_message"],
            "MANUAL_TASK",
        )
        raise

    job = bulk_task_view(task) or task
    job_id = int(task["id"])
    config["last_job_id"] = job_id
    config["last_status"] = "STARTED"
    config["last_message"] = (
        "定时检测任务已进入任务中心"
        if scheduled
        else "立即检测任务已进入任务中心"
    )
    _write(config)
    add_audit_log(
        requested_by,
        "OCI_ACCOUNT_CHECK_SCHEDULE_TRIGGERED",
        ip_address,
        (
            f"task={job_id},interval={config['account_interval_seconds']},"
            f"scheduled={int(scheduled)},source={source}"
        ),
        "MANUAL_TASK",
        str(job_id),
    )
    return job


async def run_account_check_schedule_now(
    *,
    requested_by: str,
    ip_address: str | None,
) -> dict:
    job = await _launch(
        _read(),
        requested_by=f"{requested_by}（定时页立即检测）",
        ip_address=ip_address,
        scheduled=False,
    )
    return {"job": job, "schedule": get_account_check_schedule()}


async def _loop() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            from .maintenance_state import status as maintenance_status
            if maintenance_status().get("active"):
                await asyncio.sleep(15)
                continue
            config = _read()
            if config["enabled"]:
                due = _parse(config.get("next_run_at"))
                if due is None:
                    config["next_run_at"] = _iso(_next(config))
                    config = _write(config)
                    due = _parse(config["next_run_at"])
                if due is not None and due <= _now():
                    config["next_run_at"] = _iso(_next(config))
                    config = _write(config)
                    try:
                        await _launch(
                            config,
                            requested_by="系统定时检测",
                            ip_address=None,
                            scheduled=True,
                        )
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                add_audit_log(
                    "系统定时检测",
                    "OCI_ACCOUNT_CHECK_SCHEDULE_LOOP_ERROR",
                    None,
                    f"{type(exc).__name__}: {exc}",
                    "SYSTEM_SETTING",
                )
            except Exception:
                pass
        await asyncio.sleep(15)


def start_account_check_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(
        _loop(),
        name="oci-nt-account-check-scheduler",
    )
