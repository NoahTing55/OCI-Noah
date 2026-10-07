from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from .config import settings
from .account_check_service import execute_account_check, mark_account_check_error
from .account_repository import get_account_public, list_accounts_public
from .database import add_audit_log
from .instance_batch_service import retry_instance_batch_task
from .proxy_repository import (
    decrypt_proxy_profile_url,
    get_proxy_profile,
    list_proxy_profiles,
    save_proxy_profile_test_result,
)
from .proxy_service import test_proxy_url_details
from .task_repository import (
    build_request_fingerprint,
    cancel_pending_items,
    create_task,
    finish_item,
    finish_task,
    find_active_account_conflicts,
    find_duplicate_task,
    get_current_task,
    get_task,
    get_task_items,
    is_cancel_requested,
    request_cancel,
    set_item_retry_state,
    set_task_current,
    start_item,
    start_task,
)
from .telegram_service import format_bulk_check_message, send_configured_telegram
from .account_check_alert_service import process_scheduled_account_check
from .oci_resilience import ResilientOperationError, adaptive_oci_call
from .system_resource_service import assert_task_resources


class TaskBusyError(RuntimeError):
    pass


_tasks: dict[int, asyncio.Task] = {}


def _register_task(task_id: int, coroutine) -> None:
    runtime_task = asyncio.create_task(coroutine)
    _tasks[int(task_id)] = runtime_task
    runtime_task.add_done_callback(lambda _: _tasks.pop(int(task_id), None))


def _account_snapshot(account: dict) -> dict:
    return {
        "id": int(account["id"]),
        "custom_name": account.get("custom_name"),
        "email": account.get("email"),
        "tenancy_name": account.get("tenancy_name"),
        "home_region_name": account.get("home_region_name"),
        "home_region_key": account.get("home_region_key"),
        "account_status": account.get("account_status") or "UNKNOWN",
        "last_error": account.get("last_error"),
        "last_checked_at": account.get("last_checked_at"),
    }


def _bulk_counts(results: list[dict]) -> tuple[int, int, int]:
    alive = sum(1 for item in results if item.get("account_status") == "ALIVE")
    unknown = sum(1 for item in results if item.get("account_status") == "UNKNOWN")
    abnormal = max(0, len(results) - alive - unknown)
    return alive, abnormal, unknown


def bulk_task_view(task: dict | None) -> dict | None:
    if not task:
        return None
    results = [
        item.get("result") or {}
        for item in task.get("items", [])
        if item.get("status") in {"SUCCEEDED", "FAILED"}
    ]
    alive, abnormal, unknown = _bulk_counts(results)
    return {
        "id": task["id"],
        "task_id": task["id"],
        "status": task["status"],
        "requested_by": task["requested_by"],
        "interval_seconds": int(
    (task.get("options") or {}).get("interval_seconds")
    if (task.get("options") or {}).get("interval_seconds") is not None
    else 30
),
        "total": task["total"],
        "current_index": min(task["total"], task["completed"] + (1 if task["is_active"] else 0)),
        "current_account_id": int(task["current_item_key"]) if str(task.get("current_item_key") or "").isdigit() else None,
        "current_account_name": task.get("current_item_name"),
        "next_account_at": task.get("next_item_at"),
        "alive": alive,
        "abnormal": abnormal,
        "unknown": unknown,
        "cancel_requested": task["cancel_requested"],
        "results": results,
        "error": task.get("error"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "completed_count": task["completed"],
        "progress_percent": task["progress_percent"],
        "is_active": task["is_active"],
        "deduplicated": bool(task.get("deduplicated")),
        "error_categories": task.get("error_categories") or {},
    }


async def start_account_check_task(
    *,
    requested_by: str,
    ip_address: str | None,
    interval_seconds: int,
    account_ids: list[int] | None = None,
    retry_of_task_id: int | None = None,
    idempotency_key: str | None = None,
    source: str = "manual",
) -> dict:
    interval_seconds = int(interval_seconds)
    source = str(source or "manual").strip().lower()
    if source not in {"manual", "scheduled", "schedule_now"}:
        source = "manual"
    if interval_seconds < 0:
        raise ValueError("全部账户检测间隔不能小于 0 秒")
    if interval_seconds > 86400:
        raise ValueError("全部账户检测间隔不能超过 86400 秒")

    accounts = list_accounts_public()
    if account_ids is not None:
        allowed = {int(value) for value in account_ids}
        accounts = [item for item in accounts if int(item["id"]) in allowed]
    items = [
        {
            "item_key": str(account["id"]),
            "item_name": account.get("custom_name") or str(account["id"]),
            "item_type": "OCI_ACCOUNT",
            "payload": {"account_id": int(account["id"])},
        }
        for account in accounts
    ]
    assert_task_resources(len(items))
    options = {
        "interval_seconds": interval_seconds,
        "adaptive_retry": True,
        "max_attempts": 4,
        "retry_of_task_id": retry_of_task_id,
        "source": source,
    }
    fingerprint = build_request_fingerprint("ACCOUNT_CHECK", items=items, options=options)
    duplicate = find_duplicate_task(
        request_fingerprint=fingerprint, idempotency_key=idempotency_key
    )
    if duplicate:
        return duplicate

    active = get_current_task("ACCOUNT_CHECK")
    if active:
        raise TaskBusyError(f"已有账户检测任务正在运行（#{active['id']}）")
    conflicts = find_active_account_conflicts(int(account["id"]) for account in accounts)
    conflicts = [item for item in conflicts if item.get("task_type") == "INSTANCE_BATCH"]
    if conflicts:
        task_ids = "、".join(f"#{item['id']}" for item in conflicts)
        raise TaskBusyError(f"所选租户正在执行实例批量任务（{task_ids}），请等待任务完成后再检测")

    task = create_task(
        task_type="ACCOUNT_CHECK",
        title="OCI 账户定时检测" if source == "scheduled" else "OCI 账户批量检测",
        requested_by=requested_by,
        items=items,
        options=options,
        retry_of_task_id=retry_of_task_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    if task.get("deduplicated"):
        return task
    _register_task(
        int(task["id"]),
        _run_account_check_task(
            task_id=int(task["id"]),
            requested_by=requested_by,
            ip_address=ip_address,
            interval_seconds=interval_seconds,
            source=source,
        ),
    )
    return get_task(int(task["id"])) or task


async def _run_account_check_task(
    *,
    task_id: int,
    requested_by: str,
    ip_address: str | None,
    interval_seconds: int,
    source: str = "manual",
) -> None:
    results: list[dict] = []
    try:
        start_task(task_id)
        items = get_task_items(task_id)
        if not items:
            finish_task(task_id, status="COMPLETED", summary={"alive": 0, "abnormal": 0, "unknown": 0})
            return

        for index, item in enumerate(items, start=1):
            if is_cancel_requested(task_id):
                cancel_pending_items(task_id)
                finish_task(task_id, status="CANCELLED", summary={"completed_before_cancel": len(results)})
                return
            account_id = int((item.get("payload") or {}).get("account_id") or item["item_key"])
            set_task_current(
                task_id, item_key=str(account_id), item_name=item["item_name"], status="RUNNING"
            )
            account = get_account_public(account_id)
            if not account:
                error = "OCI 账户不存在或已删除"
                start_item(int(item["id"]))
                finish_item(
                    int(item["id"]), status="FAILED",
                    result={"id": account_id, "account_status": "UNKNOWN", "last_error": error},
                    error=error, error_category="NOT_FOUND", retryable=False,
                )
                results.append({"id": account_id, "account_status": "UNKNOWN", "last_error": error})
            else:
                def before_attempt(_: int) -> None:
                    start_item(int(item["id"]))

                def on_retry(info, attempt: int, delay: int, next_at: str) -> None:
                    set_item_retry_state(
                        int(item["id"]), error_category=info.category, retryable=True,
                        next_retry_at=next_at,
                        error=f"{info.label}，第 {attempt} 次失败，{delay} 秒后重试：{info.message}",
                    )
                    set_task_current(
                        task_id, item_key=str(account_id), item_name=item["item_name"],
                        status="WAITING", next_item_at=next_at,
                    )

                try:
                    checked, attempts = await adaptive_oci_call(
                        lambda: execute_account_check(account_id),
                        max_attempts=4, before_attempt=before_attempt, on_retry=on_retry,
                        cancel_requested=lambda: is_cancel_requested(task_id),
                    )
                    set_task_current(
                        task_id, item_key=str(account_id), item_name=item["item_name"], status="RUNNING"
                    )
                    snapshot = _account_snapshot(checked)
                    snapshot["attempts"] = attempts
                    results.append(snapshot)
                    finish_item(int(item["id"]), status="SUCCEEDED", result=snapshot)
                except ResilientOperationError as exc:
                    try:
                        checked = await asyncio.to_thread(
                            mark_account_check_error,
                            account,
                            f"{exc.info.label}: {exc.info.message}",
                        )
                        snapshot = _account_snapshot(checked)
                    except Exception:
                        snapshot = {
                            "id": account_id,
                            "custom_name": account.get("custom_name"),
                            "account_status": "UNKNOWN",
                            "last_error": f"{exc.info.label}: {exc.info.message}",
                        }
                    snapshot.update({
                        "error_category": exc.info.category,
                        "error_label": exc.info.label,
                        "attempts": exc.attempts,
                    })
                    results.append(snapshot)
                    finish_item(
                        int(item["id"]), status="FAILED", result=snapshot,
                        error=snapshot.get("last_error") or exc.info.message,
                        error_category=exc.info.category, retryable=exc.info.retryable,
                    )

            if index < len(items):
                next_at = datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)
                set_task_current(
                    task_id, item_key=None, item_name=None, status="WAITING",
                    next_item_at=next_at.isoformat(),
                )
                for _ in range(interval_seconds):
                    if is_cancel_requested(task_id):
                        cancel_pending_items(task_id)
                        finish_task(task_id, status="CANCELLED", summary={"completed_before_cancel": len(results)})
                        return
                    await asyncio.sleep(1)

        alive, abnormal, unknown = _bulk_counts(results)
        final = finish_task(
            task_id,
            summary={
                "alive": alive, "abnormal": abnormal, "unknown": unknown,
                "adaptive_retry": True,
            },
        )
        # OCI-N&T V1.0.4 1.0.5-alert-health-taskcenter1: state-aware scheduled alerting.
        if source == "scheduled":
            await asyncio.to_thread(process_scheduled_account_check, final, results)
        else:
            message = format_bulk_check_message(len(results), alive, abnormal, unknown, results)
            await asyncio.to_thread(send_configured_telegram, message, "account_check")
        add_audit_log(
            requested_by, "OCI_ACCOUNTS_BULK_CHECKED", ip_address,
            f"task={task_id},total={len(results)},alive={alive},abnormal={abnormal},unknown={unknown},status={final['status']},source={source}",
            "MANUAL_TASK", str(task_id),
        )
    except asyncio.CancelledError:
        finish_task(task_id, status="INTERRUPTED", error="任务进程被终止；系统不会自动恢复 OCI 查询")
        raise
    except Exception as exc:
        finish_task(task_id, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        add_audit_log(
            requested_by, "OCI_ACCOUNTS_BULK_CHECK_FAILED", ip_address,
            f"task={task_id},{type(exc).__name__}: {exc}", "MANUAL_TASK", str(task_id),
        )


async def start_proxy_health_task(
    *,
    requested_by: str,
    ip_address: str | None,
    profile_ids: list[int] | None = None,
    retry_of_task_id: int | None = None,
    idempotency_key: str | None = None,
) -> dict:
    profiles = list_proxy_profiles()
    if profile_ids is not None:
        allowed = {int(value) for value in profile_ids}
        profiles = [item for item in profiles if int(item["id"]) in allowed]
    items = [
        {
            "item_key": str(profile["id"]),
            "item_name": profile["name"],
            "item_type": "PROXY_PROFILE",
            "payload": {"profile_id": int(profile["id"])},
        }
        for profile in profiles
    ]
    assert_task_resources(len(items))
    options = {"concurrency": 1, "retry_of_task_id": retry_of_task_id}
    fingerprint = build_request_fingerprint("PROXY_HEALTH", items=items, options=options)
    duplicate = find_duplicate_task(
        request_fingerprint=fingerprint, idempotency_key=idempotency_key
    )
    if duplicate:
        return duplicate
    active = get_current_task("PROXY_HEALTH")
    if active:
        raise TaskBusyError(f"已有代理检测任务正在运行（#{active['id']}）")
    task = create_task(
        task_type="PROXY_HEALTH",
        title="代理批量健康检测",
        requested_by=requested_by,
        items=items,
        options=options,
        retry_of_task_id=retry_of_task_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    if task.get("deduplicated"):
        return task
    _register_task(
        int(task["id"]),
        _run_proxy_health_task(
            task_id=int(task["id"]),
            requested_by=requested_by,
            ip_address=ip_address,
        ),
    )
    return get_task(int(task["id"])) or task


async def _run_proxy_health_task(
    *,
    task_id: int,
    requested_by: str,
    ip_address: str | None,
) -> None:
    try:
        start_task(task_id)
        items = get_task_items(task_id)
        if not items:
            finish_task(task_id, status="COMPLETED", summary={"available": 0, "failed": 0})
            return

        for item in items:
            if is_cancel_requested(task_id):
                cancel_pending_items(task_id)
                finish_task(task_id, status="CANCELLED")
                return
            profile_id = int((item.get("payload") or {}).get("profile_id") or item["item_key"])
            profile = get_proxy_profile(profile_id)
            if not profile:
                finish_item(int(item["id"]), status="FAILED", error="代理不存在或已删除")
                continue
            set_task_current(
                task_id,
                item_key=str(profile_id),
                item_name=profile["name"],
                status="RUNNING",
            )
            start_item(int(item["id"]))
            tested_at = datetime.now(timezone.utc).isoformat()
            try:
                proxy_url = decrypt_proxy_profile_url(profile_id)
                details = await asyncio.to_thread(test_proxy_url_details, proxy_url)
                save_proxy_profile_test_result(
                    profile_id,
                    ip_address=details["ip_address"],
                    tested_at=tested_at,
                    error=None,
                    latency_ms=details.get("latency_ms"),
                    country_code=details.get("country_code"),
                    country_name=details.get("country_name"),
                    region_name=details.get("region_name"),
                    city=details.get("city"),
                    task_id=task_id,
                )
                finish_item(int(item["id"]), status="SUCCEEDED", result=details)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                save_proxy_profile_test_result(
                    profile_id,
                    ip_address=None,
                    tested_at=tested_at,
                    error=error,
                    task_id=task_id,
                )
                finish_item(int(item["id"]), status="FAILED", error=error)

        final = finish_task(task_id)
        message = (
            "OCI-N&T 代理健康检测完成\n\n"
            f"系统版本：{settings.display_version}\n"
            f"代理总数：{final['total']}\n"
            f"成功：{final['succeeded']}\n"
            f"失败：{final['failed']}"
        )
        await asyncio.to_thread(send_configured_telegram, message, "proxy_alert")
        add_audit_log(
            requested_by,
            "PROXY_HEALTH_BATCH_TESTED",
            ip_address,
            f"task={task_id},total={final['total']},success={final['succeeded']},failed={final['failed']}",
            "MANUAL_TASK",
            str(task_id),
        )
    except asyncio.CancelledError:
        finish_task(task_id, status="INTERRUPTED", error="代理检测进程被终止")
        raise
    except Exception as exc:
        finish_task(task_id, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        add_audit_log(
            requested_by,
            "PROXY_HEALTH_BATCH_FAILED",
            ip_address,
            f"task={task_id},{type(exc).__name__}: {exc}",
            "MANUAL_TASK",
            str(task_id),
        )


async def retry_failed_task(
    *,
    task_id: int,
    requested_by: str,
    ip_address: str | None,
) -> dict:
    original = get_task(task_id)
    if not original:
        raise KeyError("任务不存在")
    failed = [item for item in original.get("items", []) if item.get("status") in {"FAILED", "INTERRUPTED"}]
    if not failed:
        raise ValueError("该任务没有失败或中断项目")
    if original["task_type"] == "ACCOUNT_CHECK":
        account_ids = [int((item.get("payload") or {}).get("account_id") or item["item_key"]) for item in failed]
        interval = int(
    (original.get("options") or {}).get("interval_seconds")
    if (original.get("options") or {}).get("interval_seconds") is not None
    else 30
)
        return await start_account_check_task(
            requested_by=requested_by,
            ip_address=ip_address,
            interval_seconds=interval,
            account_ids=account_ids,
            retry_of_task_id=task_id,
        )
    if original["task_type"] == "PROXY_HEALTH":
        profile_ids = [int((item.get("payload") or {}).get("profile_id") or item["item_key"]) for item in failed]
        return await start_proxy_health_task(
            requested_by=requested_by,
            ip_address=ip_address,
            profile_ids=profile_ids,
            retry_of_task_id=task_id,
        )
    if original["task_type"] == "INSTANCE_BATCH":
        return await retry_instance_batch_task(
            original_task=original,
            requested_by=requested_by,
            ip_address=ip_address,
        )
    raise ValueError("该任务类型暂不支持重试")


def cancel_task(task_id: int) -> bool:
    return request_cancel(task_id)
