from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from fastapi import HTTPException

from .account_repository import get_account_public
from .database import add_audit_log
from .instance_cache_repository import (
    record_account_instance_sync_error,
    replace_account_instance_cache,
    update_cached_instance,
    update_cached_public_ip,
)
from .oci_resource_service import list_instances_all_regions, replace_ephemeral_public_ip
from .oci_service import instance_action
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
    set_item_retry_state,
    set_task_current,
    start_item,
    start_task,
)
from .telegram_service import format_task_summary_message, send_configured_telegram
from .tenant_scope import require_account, require_instance_region, require_private_ip
from .oci_resilience import ResilientOperationError, adaptive_oci_call
from .system_resource_service import assert_task_resources


INSTANCE_OPERATIONS = {
    "START": "批量开机",
    "SOFTSTOP": "批量关机",
    "SOFTRESET": "批量重启",
    "REPLACE_PUBLIC_IP": "批量更换公网 IP",
    "SYNC_ACCOUNTS": "批量同步租户",
}


class InstanceBatchBusyError(RuntimeError):
    pass


_runtime_tasks: dict[int, asyncio.Task] = {}


def _register(task_id: int, coroutine) -> None:
    runtime = asyncio.create_task(coroutine)
    _runtime_tasks[int(task_id)] = runtime
    runtime.add_done_callback(lambda _: _runtime_tasks.pop(int(task_id), None))


def _http_error_text(exc: HTTPException) -> str:
    detail = exc.detail
    return str(detail if isinstance(detail, str) else detail or "请求无效")


def _normalize_operation(value: str) -> str:
    operation = str(value or "").strip().upper()
    if operation not in INSTANCE_OPERATIONS:
        raise ValueError("不支持的批量实例操作")
    return operation


def _dedupe_items(operation: str, raw_items: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    seen: set[str] = set()
    for raw in raw_items:
        account_id = int(raw.get("account_id") or 0)
        if account_id < 1:
            raise ValueError("批量任务包含无效租户")
        account = require_account(account_id)
        account_name = str(raw.get("account_name") or account.get("custom_name") or account_id)
        if operation == "SYNC_ACCOUNTS":
            key = f"account:{account_id}"
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    "item_key": key,
                    "item_name": account_name,
                    "item_type": "OCI_ACCOUNT",
                    "payload": {
                        "operation": operation,
                        "account_id": account_id,
                        "account_name": account_name,
                    },
                }
            )
            continue

        instance_id = str(raw.get("instance_id") or "").strip()
        region = str(raw.get("region") or "").strip()
        if not instance_id:
            raise ValueError("批量任务包含无效实例")
        instance = require_instance_region(account_id, instance_id, region or None)
        display_name = str(
            raw.get("display_name")
            or instance.get("display_name")
            or instance_id
        )
        private_ip_id = str(raw.get("private_ip_id") or "").strip() or None
        if operation == "REPLACE_PUBLIC_IP":
            if not private_ip_id:
                raise ValueError(f"实例 {display_name} 没有可更换的私网 IP")
            require_private_ip(account_id, private_ip_id)
        key = f"instance:{account_id}:{instance_id}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "item_key": key,
                "item_name": f"{account_name} / {display_name}",
                "item_type": "OCI_INSTANCE",
                "payload": {
                    "operation": operation,
                    "account_id": account_id,
                    "account_name": account_name,
                    "instance_id": instance_id,
                    "display_name": display_name,
                    "region": region or instance.get("region"),
                    "private_ip_id": private_ip_id,
                },
            }
        )
    if not normalized:
        raise ValueError("请至少选择一个有效项目")
    if len(normalized) > 100:
        raise ValueError("单个批量任务最多包含 100 个项目")
    return normalized


async def start_instance_batch_task(
    *,
    requested_by: str,
    ip_address: str | None,
    operation: str,
    items: list[dict],
    interval_seconds: int = 2,
    retry_of_task_id: int | None = None,
    idempotency_key: str | None = None,
) -> dict:
    from .maintenance_state import reject_new_work_during_maintenance
    reject_new_work_during_maintenance()
    operation = _normalize_operation(operation)
    interval_seconds = max(0, min(int(interval_seconds), 60))
    normalized = _dedupe_items(operation, items)
    assert_task_resources(len(normalized))
    max_attempts = {
        "SYNC_ACCOUNTS": 4,
        "START": 3,
        "SOFTSTOP": 3,
        "SOFTRESET": 2,
        "REPLACE_PUBLIC_IP": 1,
    }[operation]
    options = {
        "operation": operation,
        "interval_seconds": interval_seconds,
        "concurrency": 1,
        "adaptive_retry": True,
        "max_attempts": max_attempts,
        "retry_of_task_id": retry_of_task_id,
    }
    fingerprint = build_request_fingerprint("INSTANCE_BATCH", items=normalized, options=options)
    duplicate = find_duplicate_task(
        request_fingerprint=fingerprint, idempotency_key=idempotency_key
    )
    if duplicate:
        return duplicate

    active = get_current_task("INSTANCE_BATCH")
    if active:
        raise InstanceBatchBusyError(f"已有实例批量任务正在运行（#{active['id']}）")
    account_ids = sorted({int((item.get("payload") or {}).get("account_id") or 0) for item in normalized})
    conflicts = find_active_account_conflicts(account_ids)
    conflicts = [item for item in conflicts if item.get("task_type") == "ACCOUNT_CHECK"]
    if conflicts:
        task_ids = "、".join(f"#{item['id']}" for item in conflicts)
        raise InstanceBatchBusyError(f"所选租户正在执行账户检测任务（{task_ids}），请等待任务完成后再操作实例")
    task = create_task(
        task_type="INSTANCE_BATCH",
        title=f"OCI {INSTANCE_OPERATIONS[operation]}",
        requested_by=requested_by,
        items=normalized,
        options=options,
        retry_of_task_id=retry_of_task_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    if task.get("deduplicated"):
        return task
    _register(
        int(task["id"]),
        _run_instance_batch_task(
            task_id=int(task["id"]),
            requested_by=requested_by,
            ip_address=ip_address,
            operation=operation,
            interval_seconds=interval_seconds,
            max_attempts=max_attempts,
        ),
    )
    return get_task(int(task["id"])) or task


def _sync_account(account_id: int) -> dict:
    account = get_account_public(account_id)
    if not account:
        raise KeyError("OCI 账户不存在或已删除")
    try:
        result = list_instances_all_regions(account_id)
    except Exception as exc:
        record_account_instance_sync_error(
            account_id=account_id,
            account_name=account.get("custom_name") or str(account_id),
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    for instance in result["instances"]:
        instance["account_id"] = account_id
        instance["account_name"] = account.get("custom_name")
        instance["account_email"] = account.get("email")
    for error in result["errors"]:
        error["account_name"] = account.get("custom_name")
    replace_account_instance_cache(
        account_id=account_id,
        account_name=account.get("custom_name") or str(account_id),
        account_email=account.get("email"),
        instances=result["instances"],
        errors=result["errors"],
        regions_scanned=result["regions_scanned"],
    )
    return {
        "account_id": account_id,
        "instances": len(result["instances"]),
        "errors": len(result["errors"]),
        "regions_scanned": result["regions_scanned"],
    }


def _execute_item(operation: str, payload: dict) -> dict:
    account_id = int(payload["account_id"])
    if operation == "SYNC_ACCOUNTS":
        return _sync_account(account_id)
    instance_id = str(payload["instance_id"])
    region = payload.get("region") or None
    if operation == "REPLACE_PUBLIC_IP":
        private_ip_id = str(payload.get("private_ip_id") or "")
        result = replace_ephemeral_public_ip(account_id, private_ip_id, region)
        update_cached_public_ip(account_id, private_ip_id, result.get("new_ip"))
        return {
            "account_id": account_id,
            "instance_id": instance_id,
            "old_ip": result.get("old_ip"),
            "new_ip": result.get("new_ip"),
            "public_ip_id": result.get("public_ip_id"),
        }
    result = instance_action(account_id, instance_id, operation, region)
    lifecycle_state = {
        "START": "STARTING",
        "SOFTSTOP": "STOPPING",
        "SOFTRESET": "REBOOTING",
    }.get(operation)
    if lifecycle_state:
        update_cached_instance(account_id, instance_id, {"lifecycle_state": lifecycle_state})
    return {
        "account_id": account_id,
        "instance_id": instance_id,
        "operation": operation,
        "lifecycle_state": lifecycle_state,
        "response": result,
    }


async def _run_instance_batch_task(
    *,
    task_id: int,
    requested_by: str,
    ip_address: str | None,
    operation: str,
    interval_seconds: int,
    max_attempts: int,
) -> None:
    started = time.monotonic()
    failures: list[str] = []
    blocked_accounts: dict[int, str] = {}
    try:
        start_task(task_id)
        items = get_task_items(task_id)
        if not items:
            finish_task(task_id, status="COMPLETED", summary={"operation": operation})
            return
        for index, item in enumerate(items, start=1):
            if is_cancel_requested(task_id):
                cancel_pending_items(task_id)
                finish_task(task_id, status="CANCELLED", summary={"operation": operation})
                return
            payload = item.get("payload") or {}
            account_id = int(payload.get("account_id") or 0)
            if account_id in blocked_accounts:
                start_item(int(item["id"]))
                error = f"同一租户此前认证失败，已暂停后续项目：{blocked_accounts[account_id]}"
                failures.append(f"{item['item_name']}：{error}")
                finish_item(
                    int(item["id"]), status="SKIPPED", error=error,
                    error_category="AUTHENTICATION", retryable=False,
                )
                continue

            set_task_current(
                task_id, item_key=item["item_key"], item_name=item["item_name"], status="RUNNING"
            )

            def before_attempt(_: int) -> None:
                start_item(int(item["id"]))

            def on_retry(info, attempt: int, delay: int, next_at: str) -> None:
                set_item_retry_state(
                    int(item["id"]), error_category=info.category, retryable=True,
                    next_retry_at=next_at,
                    error=f"{info.label}，第 {attempt} 次失败，{delay} 秒后重试：{info.message}",
                )
                set_task_current(
                    task_id, item_key=item["item_key"], item_name=item["item_name"],
                    status="WAITING", next_item_at=next_at,
                )

            try:
                result, attempts = await adaptive_oci_call(
                    lambda: _execute_item(operation, payload),
                    max_attempts=max_attempts, before_attempt=before_attempt, on_retry=on_retry,
                    cancel_requested=lambda: is_cancel_requested(task_id),
                )
                set_task_current(
                    task_id, item_key=item["item_key"], item_name=item["item_name"], status="RUNNING"
                )
                result = {**result, "attempts": attempts}
                finish_item(int(item["id"]), status="SUCCEEDED", result=result)
            except ResilientOperationError as exc:
                error = f"{exc.info.label}: {exc.info.message}"
                failures.append(f"{item['item_name']}：{error}")
                if exc.info.category == "AUTHENTICATION":
                    blocked_accounts[account_id] = exc.info.message
                finish_item(
                    int(item["id"]), status="FAILED",
                    result={
                        "attempts": exc.attempts,
                        "error_category": exc.info.category,
                        "error_label": exc.info.label,
                        "status_code": exc.info.status_code,
                        "service_code": exc.info.service_code,
                    },
                    error=error, error_category=exc.info.category,
                    retryable=exc.info.retryable,
                )
            except HTTPException as exc:
                error = _http_error_text(exc)
                failures.append(f"{item['item_name']}：{error}")
                finish_item(int(item["id"]), status="FAILED", error=error, error_category="CONFIGURATION")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                failures.append(f"{item['item_name']}：{error}")
                finish_item(int(item["id"]), status="FAILED", error=error, error_category="UNKNOWN")

            if index < len(items) and interval_seconds:
                for _ in range(interval_seconds):
                    if is_cancel_requested(task_id):
                        cancel_pending_items(task_id)
                        finish_task(task_id, status="CANCELLED", summary={"operation": operation})
                        return
                    await asyncio.sleep(1)
        final = finish_task(
            task_id,
            summary={
                "operation": operation,
                "operation_label": INSTANCE_OPERATIONS[operation],
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "adaptive_retry": True,
                "blocked_accounts": len(blocked_accounts),
            },
        )
        message = format_task_summary_message(
            title=f"{INSTANCE_OPERATIONS[operation]}完成",
            total=final["total"], succeeded=final["succeeded"], failed=final["failed"],
            elapsed_seconds=time.monotonic() - started, failures=failures,
        )
        await asyncio.to_thread(send_configured_telegram, message, "instance_operation")
        add_audit_log(
            requested_by, "OCI_INSTANCE_BATCH_COMPLETED", ip_address,
            f"task={task_id},operation={operation},total={final['total']},success={final['succeeded']},failed={final['failed']}",
            "MANUAL_TASK", str(task_id),
        )
    except asyncio.CancelledError:
        finish_task(task_id, status="INTERRUPTED", error="实例批量任务进程被终止")
        raise
    except Exception as exc:
        finish_task(task_id, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        add_audit_log(
            requested_by, "OCI_INSTANCE_BATCH_FAILED", ip_address,
            f"task={task_id},{type(exc).__name__}: {exc}", "MANUAL_TASK", str(task_id),
        )


async def retry_instance_batch_task(
    *,
    original_task: dict,
    requested_by: str,
    ip_address: str | None,
) -> dict:
    failed = [item for item in original_task.get("items", []) if item.get("status") in {"FAILED", "INTERRUPTED"}]
    if not failed:
        raise ValueError("该任务没有失败或中断项目")
    options = original_task.get("options") or {}
    operation = _normalize_operation(options.get("operation"))
    raw_items = [dict(item.get("payload") or {}) for item in failed]
    return await start_instance_batch_task(
        requested_by=requested_by,
        ip_address=ip_address,
        operation=operation,
        items=raw_items,
        interval_seconds=int(options.get("interval_seconds") or 2),
        retry_of_task_id=int(original_task["id"]),
    )
