from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .database import database
from .instance_batch_service import start_instance_batch_task
from .oci_rc_service import get_instance_live
from .task_repository import get_task
from .task_service import start_account_check_task, start_proxy_health_task


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _interrupted_items(task: dict) -> list[dict]:
    return [item for item in task.get("items", []) if item.get("status") == "INTERRUPTED"]


def _save_recovery_decisions(decisions: list[dict]) -> None:
    if not decisions:
        return
    now = _now()
    with database() as connection:
        for item in decisions:
            connection.execute(
                """
                UPDATE manual_task_items
                SET recovery_state = ?, recovery_note = ?, observed_state = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    str(item.get("decision") or "UNRESOLVED"),
                    str(item.get("reason") or "")[:1000] or None,
                    str(item.get("live_state") or "")[:100] or None,
                    now,
                    int(item["item_id"]),
                ),
            )


async def preview_safe_resume(task_id: int) -> dict:
    task = get_task(task_id)
    if not task:
        raise KeyError("任务不存在")
    interrupted = _interrupted_items(task)
    if not interrupted:
        raise ValueError("该任务没有中断项目")

    if task["task_type"] in {"ACCOUNT_CHECK", "PROXY_HEALTH"}:
        decisions = [
            {
                "item_id": item["id"],
                "item_name": item["item_name"],
                "decision": "RETRY",
                "reason": "只读检测任务可安全重试",
                "payload": item.get("payload") or {},
            }
            for item in interrupted
        ]
        _save_recovery_decisions(decisions)
        return {
            "task_id": task_id,
            "task_type": task["task_type"],
            "safe": True,
            "requires_confirmation": False,
            "items": decisions,
            "generated_at": _now(),
        }

    if task["task_type"] != "INSTANCE_BATCH":
        raise ValueError("该任务类型不支持安全恢复")

    operation = str((task.get("options") or {}).get("operation") or "").upper()
    if operation == "REPLACE_PUBLIC_IP":
        decisions = [
            {
                "item_id": item["id"],
                "item_name": item["item_name"],
                "decision": "MANUAL_ONLY",
                "reason": "更换公网 IP 的执行结果可能未知，系统不会自动补执行",
                "payload": item.get("payload") or {},
            }
            for item in interrupted
        ]
        _save_recovery_decisions(decisions)
        return {
            "task_id": task_id,
            "task_type": task["task_type"],
            "operation": operation,
            "safe": False,
            "requires_confirmation": True,
            "items": decisions,
            "generated_at": _now(),
        }

    decisions = []
    for item in interrupted:
        payload = item.get("payload") or {}
        if operation == "SYNC_ACCOUNTS":
            decisions.append({
                "item_id": item["id"],
                "item_name": item["item_name"],
                "decision": "RETRY",
                "reason": "同步租户是只读操作，可安全重试",
                "payload": payload,
            })
            continue
        try:
            live = await asyncio.to_thread(
                get_instance_live,
                int(payload["account_id"]),
                str(payload["instance_id"]),
                str(payload.get("region") or ""),
            )
            state = str(live.get("lifecycle_state") or "UNKNOWN").upper()
            if operation == "START":
                decision = "ALREADY_SATISFIED" if state in {"RUNNING", "STARTING"} else "RETRY"
                reason = "实例已经运行或正在启动" if decision == "ALREADY_SATISFIED" else f"当前状态 {state}，需要补执行开机"
            elif operation == "SOFTSTOP":
                decision = "ALREADY_SATISFIED" if state in {"STOPPED", "STOPPING"} else "RETRY"
                reason = "实例已经停止或正在停止" if decision == "ALREADY_SATISFIED" else f"当前状态 {state}，需要补执行关机"
            elif operation == "SOFTRESET":
                decision = "CONFIRM_RETRY"
                reason = f"当前状态 {state} 无法证明重启是否已执行，需要人工确认"
            else:
                decision = "MANUAL_ONLY"
                reason = "未知实例操作，拒绝自动恢复"
            decisions.append({
                "item_id": item["id"],
                "item_name": item["item_name"],
                "decision": decision,
                "reason": reason,
                "live_state": state,
                "payload": payload,
            })
        except Exception as exc:
            decisions.append({
                "item_id": item["id"],
                "item_name": item["item_name"],
                "decision": "UNRESOLVED",
                "reason": f"读取实例实时状态失败：{type(exc).__name__}: {exc}",
                "payload": payload,
            })

    _save_recovery_decisions(decisions)
    return {
        "task_id": task_id,
        "task_type": task["task_type"],
        "operation": operation,
        "safe": all(item["decision"] in {"RETRY", "ALREADY_SATISFIED"} for item in decisions),
        "requires_confirmation": any(item["decision"] == "CONFIRM_RETRY" for item in decisions),
        "items": decisions,
        "generated_at": _now(),
    }


async def resume_task_safely(
    task_id: int,
    *,
    requested_by: str,
    ip_address: str | None,
    confirmation: str | None = None,
) -> dict:
    task = get_task(task_id)
    if not task:
        raise KeyError("任务不存在")
    preview = await preview_safe_resume(task_id)
    if task["task_type"] == "ACCOUNT_CHECK":
        account_ids = [
            int((item.get("payload") or {}).get("account_id") or item["item_key"])
            for item in _interrupted_items(task)
        ]
        return await start_account_check_task(
            requested_by=requested_by,
            ip_address=ip_address,
            interval_seconds=int(
    (task.get("options") or {}).get("interval_seconds")
    if (task.get("options") or {}).get("interval_seconds") is not None
    else 30
),
            account_ids=account_ids,
            retry_of_task_id=task_id,
        )
    if task["task_type"] == "PROXY_HEALTH":
        profile_ids = [
            int((item.get("payload") or {}).get("profile_id") or item["item_key"])
            for item in _interrupted_items(task)
        ]
        return await start_proxy_health_task(
            requested_by=requested_by,
            ip_address=ip_address,
            profile_ids=profile_ids,
            retry_of_task_id=task_id,
        )

    operation = str(preview.get("operation") or "")
    if operation == "REPLACE_PUBLIC_IP":
        raise ValueError("更换公网 IP 的中断结果必须人工检查，不能安全自动恢复")
    if preview.get("requires_confirmation"):
        expected = f"RESUME {task_id}"
        if str(confirmation or "").strip() != expected:
            raise ValueError(f"该操作可能重复执行，请输入确认文字：{expected}")

    retry_items = [
        item.get("payload") or {}
        for item in preview["items"]
        if item["decision"] in {"RETRY", "CONFIRM_RETRY"}
    ]
    if not retry_items:
        return {
            "resumed": False,
            "task_id": task_id,
            "message": "所有中断项目的目标状态已经满足，无需补执行",
            "preview": preview,
        }
    new_task = await start_instance_batch_task(
        requested_by=requested_by,
        ip_address=ip_address,
        operation=operation,
        items=retry_items,
        interval_seconds=int((task.get("options") or {}).get("interval_seconds") or 2),
        retry_of_task_id=task_id,
    )
    return {"resumed": True, "task": new_task, "preview": preview}
