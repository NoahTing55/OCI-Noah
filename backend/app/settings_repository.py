from cryptography.fernet import InvalidToken

from .credential_crypto import decrypt_secret, encrypt_secret
from .database import database
from .config import settings

TELEGRAM_ENABLED_KEY = "telegram_enabled"
TELEGRAM_CHAT_ID_KEY = "telegram_chat_id"
TELEGRAM_BOT_TOKEN_KEY = "telegram_bot_token"
TELEGRAM_CATEGORY_KEYS = {
    "account_check": "telegram_notify_account_check",
    "instance_operation": "telegram_notify_instance_operation",
    "launch_task": "telegram_notify_launch_task",
    "proxy_alert": "telegram_notify_proxy_alert",
    "system_backup": "telegram_notify_system_backup",
    "system_resource": "telegram_notify_system_resource",
}
BULK_CHECK_INTERVAL_SECONDS_KEY = "bulk_check_interval_seconds"
GOOGLE_OAUTH_ENABLED_KEY = "google_oauth_enabled"
GOOGLE_CLIENT_ID_KEY = "google_client_id"
GOOGLE_CLIENT_SECRET_KEY = "google_client_secret"
GOOGLE_REDIRECT_URI_KEY = "google_redirect_uri"
GOOGLE_ALLOWED_EMAILS_KEY = "google_allowed_emails"
GOOGLE_FRONTEND_RETURN_URL_KEY = "google_frontend_return_url"
BACKUP_RETENTION_DAYS_KEY = "backup_retention_days"
BACKUP_KEEP_LATEST_KEY = "backup_keep_latest"
BACKUP_MAX_COUNT_KEY = "backup_max_count"
BACKUP_SCHEDULE_ENABLED_KEY = "backup_schedule_enabled"
BACKUP_SCHEDULE_FREQUENCY_KEY = "backup_schedule_frequency"
BACKUP_SCHEDULE_LOCAL_TIME_KEY = "backup_schedule_local_time"
BACKUP_SCHEDULE_WEEKDAY_KEY = "backup_schedule_weekday"
BACKUP_SCHEDULE_TIMEZONE_OFFSET_KEY = "backup_schedule_timezone_offset_minutes"
BACKUP_SCHEDULE_LAST_SLOT_KEY = "backup_schedule_last_slot"
BACKUP_SCHEDULE_LAST_RUN_AT_KEY = "backup_schedule_last_run_at"
BACKUP_SCHEDULE_LAST_STATUS_KEY = "backup_schedule_last_status"
BACKUP_SCHEDULE_LAST_ERROR_KEY = "backup_schedule_last_error"
BACKUP_SCHEDULE_LAST_BACKUP_KEY = "backup_schedule_last_backup"


def get_setting(setting_key: str) -> str | None:
    with database() as connection:
        row = connection.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = ?",
            (setting_key,),
        ).fetchone()
    return row["setting_value"] if row else None


def set_setting(
    setting_key: str,
    setting_value: str | None,
    *,
    is_secret: bool = False,
) -> None:
    with database() as connection:
        connection.execute(
            """
            INSERT INTO system_settings
                (setting_key, setting_value, is_secret, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key)
            DO UPDATE SET setting_value = excluded.setting_value,
                          is_secret = excluded.is_secret,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (setting_key, setting_value, 1 if is_secret else 0),
        )


def load_telegram_settings(*, include_token: bool = False) -> dict:
    enabled = (get_setting(TELEGRAM_ENABLED_KEY) or "0") == "1"
    chat_id = get_setting(TELEGRAM_CHAT_ID_KEY) or ""
    encrypted_token = get_setting(TELEGRAM_BOT_TOKEN_KEY)
    bot_token = None
    if include_token and encrypted_token:
        try:
            bot_token = decrypt_secret(encrypted_token)
        except InvalidToken:
            bot_token = None
    categories = {
        name: (get_setting(key) or "1") == "1"
        for name, key in TELEGRAM_CATEGORY_KEYS.items()
    }
    return {
        "enabled": enabled,
        "chat_id": chat_id,
        "has_bot_token": bool(encrypted_token),
        "bot_token": bot_token,
        **categories,
    }


def save_telegram_settings(
    enabled: bool,
    chat_id: str,
    bot_token: str | None,
    *,
    account_check: bool = True,
    instance_operation: bool = True,
    launch_task: bool = True,
    proxy_alert: bool = True,
    system_backup: bool = True,
    system_resource: bool = True,
) -> dict:
    set_setting(TELEGRAM_ENABLED_KEY, "1" if enabled else "0")
    set_setting(TELEGRAM_CHAT_ID_KEY, chat_id.strip())
    if bot_token and bot_token.strip():
        set_setting(
            TELEGRAM_BOT_TOKEN_KEY,
            encrypt_secret(bot_token.strip()),
            is_secret=True,
        )
    values = {
        "account_check": account_check,
        "instance_operation": instance_operation,
        "launch_task": launch_task,
        "proxy_alert": proxy_alert,
        "system_backup": system_backup,
        "system_resource": system_resource,
    }
    for name, key in TELEGRAM_CATEGORY_KEYS.items():
        set_setting(key, "1" if values[name] else "0")
    return load_telegram_settings(include_token=False)


def telegram_category_enabled(category: str | None) -> bool:
    if not category:
        return True
    key = TELEGRAM_CATEGORY_KEYS.get(str(category).strip().lower())
    if not key:
        return True
    return (get_setting(key) or "1") == "1"


def get_bulk_check_interval_seconds(default: int = 30) -> int:
    try:
        value = int(get_setting(BULK_CHECK_INTERVAL_SECONDS_KEY) or default)
    except (TypeError, ValueError):
        value = default
    return max(0, min(value, 86400))


def save_bulk_check_interval_seconds(interval_seconds: int) -> int:
    value = int(interval_seconds)
    if value < 0:
        raise ValueError("全部账户检测间隔不能小于 0 秒")
    if value > 86400:
        raise ValueError("全部账户检测间隔不能超过 86400 秒")
    set_setting(BULK_CHECK_INTERVAL_SECONDS_KEY, str(value))
    return value



def load_backup_policy() -> dict:
    def read_int(key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(get_setting(key) or default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    retention_days = read_int(BACKUP_RETENTION_DAYS_KEY, 14, 1, 3650)
    keep_latest = read_int(BACKUP_KEEP_LATEST_KEY, 5, 1, 1000)
    max_count = read_int(BACKUP_MAX_COUNT_KEY, 50, 1, 5000)
    if max_count < keep_latest:
        max_count = keep_latest
    return {
        "retention_days": retention_days,
        "keep_latest": keep_latest,
        "max_count": max_count,
    }


def save_backup_policy(*, retention_days: int, keep_latest: int, max_count: int) -> dict:
    days = int(retention_days)
    keep = int(keep_latest)
    maximum = int(max_count)
    if not 1 <= days <= 3650:
        raise ValueError("备份保留天数必须在 1 到 3650 之间")
    if not 1 <= keep <= 1000:
        raise ValueError("至少保留份数必须在 1 到 1000 之间")
    if not 1 <= maximum <= 5000:
        raise ValueError("最大备份份数必须在 1 到 5000 之间")
    if maximum < keep:
        raise ValueError("最大备份份数不能小于至少保留份数")
    set_setting(BACKUP_RETENTION_DAYS_KEY, str(days))
    set_setting(BACKUP_KEEP_LATEST_KEY, str(keep))
    set_setting(BACKUP_MAX_COUNT_KEY, str(maximum))
    return load_backup_policy()


def load_backup_schedule() -> dict:
    enabled = (get_setting(BACKUP_SCHEDULE_ENABLED_KEY) or "0") == "1"
    frequency = (get_setting(BACKUP_SCHEDULE_FREQUENCY_KEY) or "DAILY").strip().upper()
    if frequency not in {"DAILY", "WEEKLY"}:
        frequency = "DAILY"
    local_time = (get_setting(BACKUP_SCHEDULE_LOCAL_TIME_KEY) or "03:00").strip()
    try:
        hour_text, minute_text = local_time.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        local_time = f"{hour:02d}:{minute:02d}"
    except (ValueError, TypeError):
        local_time = "03:00"
    try:
        weekday = int(get_setting(BACKUP_SCHEDULE_WEEKDAY_KEY) or 0)
    except (TypeError, ValueError):
        weekday = 0
    weekday = max(0, min(weekday, 6))
    try:
        offset = int(get_setting(BACKUP_SCHEDULE_TIMEZONE_OFFSET_KEY) or 480)
    except (TypeError, ValueError):
        offset = 480
    offset = max(-720, min(offset, 840))
    return {
        "enabled": enabled,
        "frequency": frequency,
        "local_time": local_time,
        "weekday": weekday,
        "timezone_offset_minutes": offset,
        "last_slot": get_setting(BACKUP_SCHEDULE_LAST_SLOT_KEY),
        "last_run_at": get_setting(BACKUP_SCHEDULE_LAST_RUN_AT_KEY),
        "last_status": get_setting(BACKUP_SCHEDULE_LAST_STATUS_KEY),
        "last_error": get_setting(BACKUP_SCHEDULE_LAST_ERROR_KEY),
        "last_backup_name": get_setting(BACKUP_SCHEDULE_LAST_BACKUP_KEY),
    }


def save_backup_schedule(
    *,
    enabled: bool,
    frequency: str,
    local_time: str,
    weekday: int,
    timezone_offset_minutes: int,
) -> dict:
    normalized_frequency = str(frequency or "DAILY").strip().upper()
    if normalized_frequency not in {"DAILY", "WEEKLY"}:
        raise ValueError("自动备份频率必须是每天或每周")
    try:
        hour_text, minute_text = str(local_time or "").strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, TypeError) as exc:
        raise ValueError("自动备份时间格式必须为 HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("自动备份时间不正确")
    day = int(weekday)
    if not 0 <= day <= 6:
        raise ValueError("每周备份星期值必须在 0 到 6 之间")
    offset = int(timezone_offset_minutes)
    if not -720 <= offset <= 840:
        raise ValueError("时区偏移必须在 -720 到 840 分钟之间")
    set_setting(BACKUP_SCHEDULE_ENABLED_KEY, "1" if enabled else "0")
    set_setting(BACKUP_SCHEDULE_FREQUENCY_KEY, normalized_frequency)
    set_setting(BACKUP_SCHEDULE_LOCAL_TIME_KEY, f"{hour:02d}:{minute:02d}")
    set_setting(BACKUP_SCHEDULE_WEEKDAY_KEY, str(day))
    set_setting(BACKUP_SCHEDULE_TIMEZONE_OFFSET_KEY, str(offset))
    return load_backup_schedule()


def normalize_google_allowed_emails(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        chunks = [str(item) for item in value]
    else:
        chunks = str(value).replace(";", "\n").replace(",", "\n").splitlines()
    result: list[str] = []
    for item in chunks:
        email = str(item).strip().lower()
        if not email:
            continue
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError(f"Google 登录邮箱格式不正确：{email}")
        if email not in result:
            result.append(email)
    return result


def _decrypt_google_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return decrypt_secret(value)
    except InvalidToken:
        return None


def load_google_auth_settings(*, include_secret: bool = False) -> dict:
    db_enabled = get_setting(GOOGLE_OAUTH_ENABLED_KEY)
    client_id = get_setting(GOOGLE_CLIENT_ID_KEY) or (settings.google_client_id or "")
    redirect_uri = get_setting(GOOGLE_REDIRECT_URI_KEY) or (settings.google_redirect_uri or "")
    frontend_return_url = get_setting(GOOGLE_FRONTEND_RETURN_URL_KEY) or (settings.google_frontend_return_url or "/")
    allowed_raw = get_setting(GOOGLE_ALLOWED_EMAILS_KEY)
    if allowed_raw is None:
        allowed_raw = settings.google_allowed_email or ""
    try:
        allowed_emails = normalize_google_allowed_emails(allowed_raw)
    except ValueError:
        allowed_emails = []
    encrypted_secret = get_setting(GOOGLE_CLIENT_SECRET_KEY)
    secret = _decrypt_google_secret(encrypted_secret) if encrypted_secret else (settings.google_client_secret or None)
    configured = bool(client_id and secret and redirect_uri and allowed_emails)
    default_enabled = configured
    enabled = (db_enabled == "1") if db_enabled is not None else default_enabled
    payload = {
        "enabled": bool(enabled),
        "configured": configured,
        "client_id": client_id,
        "has_client_secret": bool(secret),
        "redirect_uri": redirect_uri,
        "frontend_return_url": frontend_return_url,
        "allowed_emails": allowed_emails,
        "source": "database" if any(get_setting(k) is not None for k in (
            GOOGLE_OAUTH_ENABLED_KEY, GOOGLE_CLIENT_ID_KEY, GOOGLE_CLIENT_SECRET_KEY,
            GOOGLE_REDIRECT_URI_KEY, GOOGLE_ALLOWED_EMAILS_KEY, GOOGLE_FRONTEND_RETURN_URL_KEY,
        )) else "environment",
    }
    if include_secret:
        payload["client_secret"] = secret
    return payload


def save_google_auth_settings(*, enabled: bool, client_id: str, client_secret: str | None,
                              redirect_uri: str, frontend_return_url: str,
                              allowed_emails) -> dict:
    from urllib.parse import urlparse

    client_id = (client_id or "").strip()
    redirect_uri = (redirect_uri or "").strip()
    frontend_return_url = (frontend_return_url or "").strip() or "/"
    emails = normalize_google_allowed_emails(allowed_emails)

    if enabled and not client_id:
        raise ValueError("启用 Gmail 登录前必须填写 Google Client ID")
    current = load_google_auth_settings(include_secret=True)
    retained_secret = current.get("client_secret")
    secret = (client_secret or "").strip() or retained_secret
    if enabled and not secret:
        raise ValueError("启用 Gmail 登录前必须填写 Google Client Secret")
    if enabled and not redirect_uri:
        raise ValueError("启用 Gmail 登录前必须填写回调地址")
    if enabled and not emails:
        raise ValueError("启用 Gmail 登录前至少填写一个允许登录的邮箱")

    for label, url, allow_relative in (
        ("Google 回调地址", redirect_uri, False),
        ("登录完成返回地址", frontend_return_url, True),
    ):
        if not url:
            continue
        parsed = urlparse(url)
        if allow_relative and url.startswith("/"):
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{label}必须是完整 HTTP/HTTPS 地址")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError(f"{label}在非本机环境必须使用 HTTPS")
        if parsed.fragment:
            raise ValueError(f"{label}不能包含 # 片段")
    if redirect_uri and not redirect_uri.rstrip("/").endswith("/api/v1/auth/google/callback"):
        raise ValueError("Google 回调地址必须以 /api/v1/auth/google/callback 结尾")

    set_setting(GOOGLE_OAUTH_ENABLED_KEY, "1" if enabled else "0")
    set_setting(GOOGLE_CLIENT_ID_KEY, client_id)
    if client_secret and client_secret.strip():
        set_setting(GOOGLE_CLIENT_SECRET_KEY, encrypt_secret(client_secret.strip()), is_secret=True)
    set_setting(GOOGLE_REDIRECT_URI_KEY, redirect_uri)
    set_setting(GOOGLE_ALLOWED_EMAILS_KEY, "\n".join(emails))
    set_setting(GOOGLE_FRONTEND_RETURN_URL_KEY, frontend_return_url)
    return load_google_auth_settings(include_secret=False)
