from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone

from .config import settings
from .settings_repository import get_setting, set_setting
from .telegram_service import send_configured_telegram

FEATURE = "1.0.5-scheduled-alert1"
POLICY_KEY = "account_check_alert_policy_v1"
STATE_KEY = "account_check_alert_state_v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_alert_policy(value: dict | None) -> dict:
    src = dict(value or {})
    mode = str(src.get("notify_mode") or "abnormal_only").strip().lower()
    if mode not in {"abnormal_only", "all", "silent"}:
        raise ValueError("通知方式只能是 abnormal_only、all 或 silent")
    threshold = int(src.get("consecutive_failures", 2))
    if not 1 <= threshold <= 10:
        raise ValueError("连续异常次数必须在 1–10 次之间")
    return {
        "notify_mode": mode,
        "consecutive_failures": threshold,
        "recovery_notify": bool(src.get("recovery_notify", True)),
    }


def _load_json(key: str, default: dict) -> dict:
    raw = get_setting(key)
    if not raw:
        return deepcopy(default)
    try:
        data = json.loads(raw)
    except Exception:
        return deepcopy(default)
    return data if isinstance(data, dict) else deepcopy(default)


def _save_json(key: str, value: dict) -> None:
    set_setting(
        key,
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        is_secret=False,
    )


def load_alert_policy() -> dict:
    return normalize_alert_policy(_load_json(POLICY_KEY, {}))


def save_alert_policy(value: dict) -> dict:
    policy = normalize_alert_policy(value)
    _save_json(POLICY_KEY, policy)
    return policy


def _load_state() -> dict:
    data = _load_json(
        STATE_KEY,
        {"accounts": {}, "last_processed_at": None, "last_task_id": None},
    )
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        accounts = {}
    return {
        "accounts": accounts,
        "last_processed_at": data.get("last_processed_at"),
        "last_task_id": data.get("last_task_id"),
    }


def get_alert_state_public() -> dict:
    state = _load_state()
    accounts = state["accounts"]
    alerted = sum(1 for item in accounts.values() if isinstance(item, dict) and item.get("alerted"))
    pending = sum(
        1
        for item in accounts.values()
        if isinstance(item, dict)
        and int(item.get("consecutive_failures") or 0) > 0
        and not item.get("alerted")
    )
    return {
        "feature": FEATURE,
        "tracked_accounts": len(accounts),
        "alerted_accounts": alerted,
        "pending_accounts": pending,
        "last_processed_at": state.get("last_processed_at"),
        "last_task_id": state.get("last_task_id"),
    }


def _duration_seconds(task: dict) -> int | None:
    def parse(value):
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

    start = parse(task.get("started_at") or task.get("created_at"))
    end = parse(task.get("finished_at"))
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def _duration_text(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def _status_label(status: str) -> str:
    labels = {
        "ALIVE": "正常",
        "UNKNOWN": "未知",
        "ERROR": "错误",
        "INVALID": "凭据无效",
        "FORBIDDEN": "权限拒绝",
    }
    return labels.get(status, status or "未知")


def process_scheduled_account_check(task: dict, results: list[dict]) -> dict:
    policy = load_alert_policy()
    old_state = _load_state()
    old_accounts = deepcopy(old_state["accounts"])
    accounts = deepcopy(old_accounts)
    threshold = int(policy["consecutive_failures"])
    new_alerts: list[dict] = []
    recoveries: list[dict] = []
    seen: set[str] = set()

    for result in results:
        account_id = result.get("id")
        if account_id in (None, ""):
            continue
        key = str(int(account_id))
        seen.add(key)
        status = str(result.get("account_status") or "UNKNOWN").upper()
        previous = accounts.get(key)
        if not isinstance(previous, dict):
            previous = {
                "consecutive_failures": 0,
                "alerted": False,
                "last_status": "UNKNOWN",
                "last_error": None,
                "updated_at": None,
            }

        current = dict(previous)
        current["last_status"] = status
        current["last_error"] = str(result.get("last_error") or "")[:500] or None
        current["updated_at"] = _now()

        if status == "ALIVE":
            if bool(previous.get("alerted")):
                recoveries.append(
                    {
                        "id": int(account_id),
                        "name": result.get("custom_name") or f"账户 {account_id}",
                        "status": status,
                    }
                )
            current["consecutive_failures"] = 0
            current["alerted"] = False
        else:
            count = int(previous.get("consecutive_failures") or 0) + 1
            current["consecutive_failures"] = count
            if count >= threshold and not bool(previous.get("alerted")):
                current["alerted"] = True
                new_alerts.append(
                    {
                        "id": int(account_id),
                        "name": result.get("custom_name") or f"账户 {account_id}",
                        "status": status,
                        "error": str(result.get("last_error") or "")[:240],
                        "count": count,
                    }
                )
        accounts[key] = current

    for key in list(accounts):
        if key not in seen:
            accounts.pop(key, None)

    alive = sum(1 for item in results if str(item.get("account_status") or "").upper() == "ALIVE")
    unknown = sum(1 for item in results if str(item.get("account_status") or "").upper() == "UNKNOWN")
    abnormal = max(0, len(results) - alive - unknown)

    mode = policy["notify_mode"]
    include_recovery = bool(policy["recovery_notify"])
    should_notify = (
        mode == "all"
        or (
            mode == "abnormal_only"
            and (bool(new_alerts) or (include_recovery and bool(recoveries)))
        )
    )

    sent = None
    send_message = ""
    if should_notify and mode != "silent":
        lines = [
            "OCI-N&T API 定时检测",
            "",
            f"系统版本：{settings.display_version}",
            f"任务：#{task.get('id') or '—'}",
            f"结果：{alive} 正常 / {abnormal} 异常 / {unknown} 未知",
            f"耗时：{_duration_text(_duration_seconds(task))}",
            f"告警阈值：连续 {threshold} 次异常",
        ]
        if new_alerts:
            lines.extend(["", f"新告警（{len(new_alerts)}）："])
            for item in new_alerts[:12]:
                copy = f"- {item['name']}：{_status_label(item['status'])} · 连续 {item['count']} 次"
                if item["error"]:
                    copy += f" · {item['error']}"
                lines.append(copy)
            if len(new_alerts) > 12:
                lines.append(f"- 另有 {len(new_alerts) - 12} 个新告警")
        if include_recovery and recoveries:
            lines.extend(["", f"恢复正常（{len(recoveries)}）："])
            for item in recoveries[:12]:
                lines.append(f"- {item['name']}")
            if len(recoveries) > 12:
                lines.append(f"- 另有 {len(recoveries) - 12} 个恢复账户")
        if mode == "all" and not new_alerts and not (include_recovery and recoveries):
            lines.extend(["", "本次无新增告警或恢复变化。"])
        send_message = "\n".join(lines)
        sent, _ = send_configured_telegram(send_message, "account_check")

    if should_notify and sent is False:
        for item in new_alerts:
            key = str(item["id"])
            if key in accounts:
                accounts[key]["alerted"] = False
        for item in recoveries:
            key = str(item["id"])
            if key in old_accounts:
                accounts[key] = old_accounts[key]

    state = {
        "accounts": accounts,
        "last_processed_at": _now(),
        "last_task_id": int(task["id"]) if task.get("id") not in (None, "") else None,
    }
    _save_json(STATE_KEY, state)

    return {
        "policy": policy,
        "new_alerts": len(new_alerts),
        "recoveries": len(recoveries),
        "notification_attempted": bool(should_notify and mode != "silent"),
        "notification_sent": sent,
        "message": send_message,
    }
