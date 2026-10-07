import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .config import settings
from .settings_repository import load_telegram_settings, telegram_category_enabled


ACCOUNT_TYPE_LABELS = {
    "PERSONAL_FREE": "个人免费号",
    "PERSONAL_UPGRADED": "个人升级号",
    "UNKNOWN": "待识别",
}


def account_status_label(status: str | None) -> str:
    if status == "ALIVE":
        return "有效"
    if not status or status == "UNKNOWN":
        return "待检测"
    return "失效"


def account_type_label(account_type: str | None) -> str:
    return ACCOUNT_TYPE_LABELS.get(account_type or "UNKNOWN", "待识别")


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "OCI-NT/1.0"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        if response_data.get("ok"):
            return True, "Telegram 消息发送成功"
        return False, response_data.get("description", "Telegram 返回未知错误")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            return False, body.get("description", str(exc))
        except Exception:
            return False, str(exc)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def send_configured_telegram(
    text: str,
    category: str | None = None,
) -> tuple[bool, str]:
    config = load_telegram_settings(include_token=True)
    if not config["enabled"]:
        return False, "Telegram 通知尚未启用"
    if category and not telegram_category_enabled(category):
        return False, f"Telegram {category} 通知已关闭"
    if not config["bot_token"]:
        return False, "尚未配置 Telegram Bot Token"
    if not config["chat_id"]:
        return False, "尚未配置 Telegram Chat ID"
    return send_telegram_message(config["bot_token"], config["chat_id"], text)


def format_task_summary_message(
    *,
    title: str,
    total: int,
    succeeded: int,
    failed: int,
    elapsed_seconds: float | int | None = None,
    failures: list[str] | None = None,
) -> str:
    elapsed = max(0, int(elapsed_seconds or 0))
    minutes, seconds = divmod(elapsed, 60)
    elapsed_text = f"{minutes}分{seconds}秒" if minutes else f"{seconds}秒"
    lines = [
        f"OCI-N&T {title}",
        "",
        f"系统版本：{settings.display_version}",
        f"总数：{int(total)}",
        f"成功：{int(succeeded)}",
        f"失败：{int(failed)}",
        f"耗时：{elapsed_text}",
    ]
    clean_failures = [str(item).strip() for item in (failures or []) if str(item).strip()]
    if clean_failures:
        lines.extend(["", "主要失败原因："])
        for index, item in enumerate(clean_failures[:5], start=1):
            lines.append(f"{index}. {item[:300]}")
    return "\n".join(lines)


def format_single_check_message(account: dict) -> str:
    survival_days = account.get("survival_days")
    survival_text = f"{survival_days} 天" if survival_days is not None else "待获取"
    region = account.get("home_region_name") or account.get("region") or "未知"
    return "\n".join(
        [
            "OCI-N&T 单账户检测",
            "",
            f"名称：{account.get('custom_name') or '未知'}",
            f"完整邮箱：{account.get('email') or '未读取'}",
            f"租户名称：{account.get('tenancy_name') or '未读取'}",
            f"主区域：{region}",
            "账户类型：" + account_type_label(account.get("account_type")),
            f"存活时间：{survival_text}",
            "状态：" + account_status_label(account.get("account_status")),
            "检测时间："
            + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        ]
    )


def format_bulk_check_message(
    total: int,
    alive: int,
    abnormal: int,
    unknown: int,
    accounts: list[dict],
) -> str:
    if total == 0:
        return "OCI 全部检测完成：当前没有账户。"

    abnormal_accounts = [
        account
        for account in accounts
        if account.get("account_status") not in ("ALIVE", "UNKNOWN", None)
    ]

    if not abnormal_accounts:
        suffix = f"，待识别 {unknown} 个" if unknown else ""
        return f"OCI 全部检测完成：全部正常，共 {total} 个账户{suffix}。"

    lines = [
        "OCI 全部检测发现异常",
        "",
        f"共 {total} 个账户，有效 {alive} 个，异常 {abnormal} 个。",
        "",
    ]

    for index, account in enumerate(abnormal_accounts, start=1):
        lines.extend(
            [
                f"{index}. {account.get('custom_name') or '未命名账户'}",
                f"租户名称：{account.get('tenancy_name') or '未读取'}",
                "状态：" + account_status_label(account.get("account_status")),
            ]
        )
        if account.get("last_error"):
            lines.append(f"错误：{account['last_error'][:300]}")
        lines.append("")

    return "\n".join(lines).strip()


def format_monitor_change_message(
    *,
    total: int,
    alive: int,
    abnormal: int,
    unknown: int,
    changes: list[dict],
) -> str:
    lines = [
        "OCI-N&T 自动检测状态变化",
        "",
        f"共 {total} 个账户，有效 {alive} 个，异常 {abnormal} 个，待检测 {unknown} 个。",
        "",
    ]

    for index, change in enumerate(changes, start=1):
        account = change["account"]
        old_status = account_status_label(change.get("old_status"))
        new_status = account_status_label(change.get("new_status"))
        lines.extend(
            [
                f"{index}. {account.get('custom_name') or '未命名账户'}",
                f"租户名称：{account.get('tenancy_name') or '未读取'}",
                f"状态变化：{old_status} -> {new_status}",
            ]
        )
        if change.get("new_status") not in ("ALIVE", "UNKNOWN", None):
            last_error = account.get("last_error")
            if last_error:
                lines.append(f"错误：{last_error[:300]}")
        lines.append("")

    return "\n".join(lines).strip()
