from __future__ import annotations

from datetime import datetime, timezone

from .account_repository import list_accounts_public

FEATURE = "1.0.5-account-health1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _has(text: str, *needles: str) -> bool:
    lower = text.lower()
    return any(needle.lower() in lower for needle in needles)


def score_account_health(account: dict, *, now: datetime | None = None) -> dict:
    now = (now or _now()).astimezone(timezone.utc)
    status = str(account.get("account_status") or "UNKNOWN").upper()
    score = {
        "ALIVE": 100,
        "UNKNOWN": 70,
        "ERROR": 45,
        "FORBIDDEN": 25,
        "INVALID": 15,
    }.get(status, 55)
    reasons: list[str] = []

    if status == "ALIVE":
        reasons.append("最近一次 OCI API 检测正常")
    elif status == "UNKNOWN":
        reasons.append("账户尚未得到明确的有效/失效结果")
    elif status == "INVALID":
        reasons.append("OCI 凭据无效")
    elif status == "FORBIDDEN":
        reasons.append("OCI 返回权限拒绝")
    else:
        reasons.append(f"最近检测状态：{status}")

    error = str(account.get("last_error") or "")
    if error:
        if _has(error, "429", "too many requests", "rate limit", "throttl"):
            score -= 12
            reasons.append("最近出现 OCI 限流")
        if _has(
            error,
            "timeout",
            "timed out",
            "connection",
            "network",
            "dns",
            "name resolution",
            "proxyerror",
            "read timed out",
        ):
            score -= 15
            reasons.append("最近出现网络、代理或超时错误")
        if _has(error, "401", "unauthorized", "notauthenticated"):
            score -= 20
            reasons.append("最近出现身份认证错误")
        if _has(error, "403", "forbidden", "notauthorized"):
            score -= 15
            reasons.append("最近出现权限错误")

    if bool(account.get("proxy_enabled")):
        proxy_error = str(account.get("proxy_last_error") or "")
        proxy_tested = _parse_time(account.get("proxy_last_test_at"))
        if proxy_error:
            score -= 18
            reasons.append("独立代理最近检测异常")
        elif proxy_tested is None:
            score -= 6
            reasons.append("独立代理尚未完成测试")

    checked_at = _parse_time(account.get("last_checked_at"))
    if checked_at is None:
        score = min(score, 70)
        reasons.append("暂无账户检测时间")
    else:
        age_hours = max(0.0, (now - checked_at).total_seconds() / 3600)
        if age_hours > 168:
            score -= 20
            reasons.append("最近一次账户检测已超过 7 天")
        elif age_hours > 72:
            score -= 10
            reasons.append("最近一次账户检测已超过 3 天")

    score = max(0, min(100, int(round(score))))
    if status in {"INVALID", "FORBIDDEN"} or score < 50:
        level, label = "CRITICAL", "异常"
    elif status != "ALIVE" or score < 85:
        level, label = "WARNING", "警告"
    else:
        level, label = "HEALTHY", "健康"

    return {
        "id": int(account["id"]),
        "custom_name": account.get("custom_name"),
        "account_status": status,
        "health_score": score,
        "health_level": level,
        "health_label": label,
        "health_reasons": reasons[:6],
        "last_checked_at": account.get("last_checked_at"),
    }


def get_account_health_snapshot() -> dict:
    now = _now()
    rows = [score_account_health(item, now=now) for item in list_accounts_public()]
    healthy = sum(1 for item in rows if item["health_level"] == "HEALTHY")
    warning = sum(1 for item in rows if item["health_level"] == "WARNING")
    critical = sum(1 for item in rows if item["health_level"] == "CRITICAL")
    average = round(sum(item["health_score"] for item in rows) / len(rows), 1) if rows else 0.0
    return {
        "feature": FEATURE,
        "generated_at": now.isoformat(),
        "source": "sqlite-cache",
        "oci_queries": False,
        "summary": {
            "total": len(rows),
            "healthy": healthy,
            "warning": warning,
            "critical": critical,
            "average_score": average,
        },
        "accounts": rows,
    }
