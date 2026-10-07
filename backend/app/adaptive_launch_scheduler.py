from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import time
from dataclasses import asdict, dataclass, fields
from typing import Any, Callable

from .database import database

# OCI-N&T V1.0.4 adaptive-launch-c2
SETTING_KEY = "launch_scheduler_config_v1"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool
    global_concurrency: int
    tenant_concurrency: int
    start_delay_min: float
    start_delay_max: float
    jitter_min: float
    jitter_max: float
    backoff_base: float
    backoff_multiplier: float
    backoff_max: float
    backoff_jitter_ratio: float
    rate_limit_multiplier: float
    success_recovery_threshold: int
    proxy_pause_seconds: float


DEFAULT_CONFIG = SchedulerConfig(
    enabled=_env_bool("OCI_NT_LAUNCH_SCHEDULER_ENABLED", True),
    global_concurrency=_env_int("OCI_NT_LAUNCH_GLOBAL_CONCURRENCY", 4, 1, 32),
    tenant_concurrency=_env_int("OCI_NT_LAUNCH_TENANT_CONCURRENCY", 1, 1, 8),
    start_delay_min=_env_float("OCI_NT_LAUNCH_START_DELAY_MIN", 1.0, 0.0, 60.0),
    start_delay_max=_env_float("OCI_NT_LAUNCH_START_DELAY_MAX", 5.0, 0.0, 120.0),
    jitter_min=_env_float("OCI_NT_LAUNCH_JITTER_MIN", 0.5, 0.0, 30.0),
    jitter_max=_env_float("OCI_NT_LAUNCH_JITTER_MAX", 2.5, 0.0, 60.0),
    backoff_base=_env_float("OCI_NT_LAUNCH_BACKOFF_BASE", 2.0, 0.5, 60.0),
    backoff_multiplier=_env_float("OCI_NT_LAUNCH_BACKOFF_MULTIPLIER", 2.0, 1.1, 4.0),
    backoff_max=_env_float("OCI_NT_LAUNCH_BACKOFF_MAX", 60.0, 5.0, 900.0),
    backoff_jitter_ratio=_env_float("OCI_NT_LAUNCH_BACKOFF_JITTER", 0.30, 0.0, 0.90),
    rate_limit_multiplier=_env_float("OCI_NT_LAUNCH_429_MULTIPLIER", 2.0, 1.0, 5.0),
    success_recovery_threshold=_env_int("OCI_NT_LAUNCH_SUCCESS_RECOVERY", 5, 1, 100),
    proxy_pause_seconds=_env_float("OCI_NT_LAUNCH_PROXY_PAUSE", 120.0, 15.0, 1800.0),
)

CONFIG = DEFAULT_CONFIG
_config_loaded = False


@dataclass
class TenantState:
    failure_streak: int = 0
    rate_limit_streak: int = 0
    success_streak: int = 0
    penalty_seconds: float = 0.0
    defer_until_monotonic: float = 0.0
    last_reason: str = ""
    last_status: int = 0
    last_code: str = ""
    total_success: int = 0
    total_failure: int = 0
    total_429: int = 0
    total_proxy_pause: int = 0


_states: dict[int, TenantState] = {}
_started_jobs: set[tuple[int, int]] = set()
_global_semaphores: dict[tuple[int, int], asyncio.Semaphore] = {}
_tenant_semaphores: dict[tuple[int, int, int], asyncio.Semaphore] = {}
_config_generation = 1
_cadence_salt = os.urandom(16)


def _tenant_cadence_factor(account_id: int) -> float:
    """Return a process-stable, tenant-specific timing factor (0.86..1.14).

    This deliberately differentiates OCI-N&T request cadence without changing
    signed OCI headers, TLS behavior, SDK identity, or request semantics.
    """
    digest = hashlib.blake2s(
        str(int(account_id)).encode("ascii"),
        key=_cadence_salt,
        digest_size=2,
    ).digest()
    ratio = int.from_bytes(digest, "big") / 65535
    return 0.86 + (ratio * 0.28)


def _normalize_config(payload: dict[str, Any], base: SchedulerConfig | None = None) -> SchedulerConfig:
    source = asdict(base or DEFAULT_CONFIG)
    allowed = {item.name for item in fields(SchedulerConfig)}
    for key, value in dict(payload or {}).items():
        if key in allowed:
            source[key] = value

    def integer(key: str, lo: int, hi: int) -> int:
        try:
            value = int(source[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须是整数")
        if not lo <= value <= hi:
            raise ValueError(f"{key} 必须在 {lo} ~ {hi} 之间")
        return value

    def number(key: str, lo: float, hi: float) -> float:
        try:
            value = float(source[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须是数字")
        if not lo <= value <= hi:
            raise ValueError(f"{key} 必须在 {lo:g} ~ {hi:g} 之间")
        return value

    enabled_raw = source.get("enabled", True)
    enabled = enabled_raw.strip().lower() not in {"0", "false", "no", "off"} if isinstance(enabled_raw, str) else bool(enabled_raw)

    config = SchedulerConfig(
        enabled=enabled,
        global_concurrency=integer("global_concurrency", 1, 32),
        tenant_concurrency=integer("tenant_concurrency", 1, 8),
        start_delay_min=number("start_delay_min", 0.0, 60.0),
        start_delay_max=number("start_delay_max", 0.0, 120.0),
        jitter_min=number("jitter_min", 0.0, 30.0),
        jitter_max=number("jitter_max", 0.0, 60.0),
        backoff_base=number("backoff_base", 0.5, 60.0),
        backoff_multiplier=number("backoff_multiplier", 1.1, 4.0),
        backoff_max=number("backoff_max", 5.0, 900.0),
        backoff_jitter_ratio=number("backoff_jitter_ratio", 0.0, 0.90),
        rate_limit_multiplier=number("rate_limit_multiplier", 1.0, 5.0),
        success_recovery_threshold=integer("success_recovery_threshold", 1, 100),
        proxy_pause_seconds=number("proxy_pause_seconds", 15.0, 1800.0),
    )
    if config.start_delay_max < config.start_delay_min:
        raise ValueError("启动错峰最大值不能小于最小值")
    if config.jitter_max < config.jitter_min:
        raise ValueError("Jitter 最大值不能小于最小值")
    if config.backoff_max < config.backoff_base:
        raise ValueError("最大退避不能小于初始退避")
    return config


def _ensure_config_loaded() -> SchedulerConfig:
    global CONFIG, _config_loaded
    if _config_loaded:
        return CONFIG
    try:
        with database() as connection:
            row = connection.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key = ?",
                (SETTING_KEY,),
            ).fetchone()
        if row:
            try:
                raw = row["setting_value"]
            except Exception:
                raw = row[0]
            CONFIG = _normalize_config(json.loads(raw or "{}"), DEFAULT_CONFIG)
        _config_loaded = True
    except Exception:
        pass
    return CONFIG


def _persist_config(config: SchedulerConfig) -> None:
    value = json.dumps(asdict(config), ensure_ascii=False, separators=(",", ":"))
    with database() as connection:
        connection.execute(
            """
            INSERT INTO system_settings(setting_key, setting_value, is_secret, updated_at)
            VALUES (?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                is_secret = 0,
                updated_at = CURRENT_TIMESTAMP
            """,
            (SETTING_KEY, value),
        )
        connection.commit()


def _apply_config(config: SchedulerConfig) -> None:
    global CONFIG, _config_loaded, _config_generation
    CONFIG = config
    _config_loaded = True
    _config_generation += 1
    _global_semaphores.clear()
    _tenant_semaphores.clear()


def update_scheduler_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = _normalize_config(payload, _ensure_config_loaded())
    _persist_config(config)
    _apply_config(config)
    return scheduler_settings_payload()


def reset_scheduler_config() -> dict[str, Any]:
    with database() as connection:
        connection.execute("DELETE FROM system_settings WHERE setting_key = ?", (SETTING_KEY,))
        connection.commit()
    _apply_config(DEFAULT_CONFIG)
    return scheduler_settings_payload()


def scheduler_defaults() -> dict[str, Any]:
    return asdict(DEFAULT_CONFIG)


def scheduler_config() -> dict[str, Any]:
    return asdict(_ensure_config_loaded())


def scheduler_settings_payload() -> dict[str, Any]:
    return {"settings": scheduler_config(), "defaults": scheduler_defaults(), "persistence": "sqlite", "setting_key": SETTING_KEY}


def _state(account_id: int) -> TenantState:
    key = int(account_id)
    if key not in _states:
        _states[key] = TenantState()
    return _states[key]


def _uniform(low: float, high: float) -> float:
    if high < low:
        low, high = high, low
    return low if high <= low else random.SystemRandom().uniform(low, high)


def _global_semaphore() -> asyncio.Semaphore:
    config = _ensure_config_loaded()
    key = (id(asyncio.get_running_loop()), _config_generation)
    if key not in _global_semaphores:
        _global_semaphores[key] = asyncio.Semaphore(config.global_concurrency)
    return _global_semaphores[key]


def _tenant_semaphore(account_id: int) -> asyncio.Semaphore:
    config = _ensure_config_loaded()
    key = (id(asyncio.get_running_loop()), _config_generation, int(account_id))
    if key not in _tenant_semaphores:
        _tenant_semaphores[key] = asyncio.Semaphore(config.tenant_concurrency)
    return _tenant_semaphores[key]


def _exception_details(exc: Exception) -> tuple[int, str, str, bool]:
    status = int(getattr(exc, "status", 0) or 0)
    code = str(getattr(exc, "code", "") or "")
    text = " ".join((type(exc).__name__, str(getattr(exc, "message", "") or ""), str(exc))).lower()
    tokens = ("proxyerror", "proxy error", "socks", "proxy connection", "cannot connect to proxy", "tunnel connection failed")
    return status, code, text, any(token in text for token in tokens)


def _backoff_seconds(streak: int, multiplier: float = 1.0) -> float:
    config = _ensure_config_loaded()
    exponent = max(0, min(int(streak) - 1, 12))
    raw = min(config.backoff_max, config.backoff_base * (config.backoff_multiplier ** exponent) * multiplier)
    if config.backoff_jitter_ratio > 0:
        raw *= _uniform(max(0.05, 1.0 - config.backoff_jitter_ratio), 1.0 + config.backoff_jitter_ratio)
    return min(config.backoff_max, max(config.backoff_base, raw))


async def adaptive_initial_stagger(account_id: int, job_id: int) -> float:
    config = _ensure_config_loaded()
    if not config.enabled:
        return 0.0
    key = (int(account_id), int(job_id))
    if key in _started_jobs:
        return 0.0
    _started_jobs.add(key)
    delay = _uniform(config.start_delay_min, config.start_delay_max) * _tenant_cadence_factor(account_id)
    if delay > 0:
        await asyncio.sleep(delay)
    return delay


async def _wait_for_tenant_window(account_id: int) -> float:
    state = _state(account_id)
    remaining = max(0.0, state.defer_until_monotonic - time.monotonic())
    if remaining > 0:
        await asyncio.sleep(remaining)
    return remaining


async def adaptive_launch_call(func: Callable[..., Any], account_id: int, *args: Any, **kwargs: Any) -> Any:
    config = _ensure_config_loaded()
    if not config.enabled:
        return await asyncio.to_thread(func, account_id, *args, **kwargs)
    await _wait_for_tenant_window(account_id)
    jitter = _uniform(config.jitter_min, config.jitter_max) * _tenant_cadence_factor(account_id)
    if jitter > 0:
        await asyncio.sleep(jitter)
    async with _tenant_semaphore(account_id):
        async with _global_semaphore():
            try:
                result = await asyncio.to_thread(func, account_id, *args, **kwargs)
            except Exception as exc:
                record_failure(account_id, exc)
                raise
            record_success(account_id)
            return result


def record_failure(account_id: int, exc: Exception) -> None:
    config = _ensure_config_loaded()
    if not config.enabled:
        return
    state = _state(account_id)
    state.total_failure += 1
    state.success_streak = 0
    state.failure_streak = min(state.failure_streak + 1, 100)
    status, code, text, is_proxy_error = _exception_details(exc)
    state.last_status, state.last_code = status, code
    now = time.monotonic()
    if is_proxy_error:
        delay = max(config.proxy_pause_seconds, _backoff_seconds(state.failure_streak))
        state.penalty_seconds = max(state.penalty_seconds, min(config.backoff_max, delay))
        state.defer_until_monotonic = max(state.defer_until_monotonic, now + delay)
        state.last_reason = "proxy_pause"
        state.total_proxy_pause += 1
        return
    if status == 429 or code == "TooManyRequests" or "too many requests" in text:
        state.rate_limit_streak = min(state.rate_limit_streak + 1, 100)
        state.total_429 += 1
        delay = _backoff_seconds(max(state.failure_streak, state.rate_limit_streak), config.rate_limit_multiplier)
        state.penalty_seconds = max(state.penalty_seconds, delay)
        state.defer_until_monotonic = max(state.defer_until_monotonic, now + delay)
        state.last_reason = "rate_limit"
        return
    timeout_like = any(token in text for token in ("timeout", "timed out", "connection reset", "connection aborted", "connectionerror"))
    if timeout_like or status in {500, 502, 503, 504} or code == "InternalError":
        delay = _backoff_seconds(state.failure_streak)
        state.penalty_seconds = max(state.penalty_seconds, delay)
        state.defer_until_monotonic = max(state.defer_until_monotonic, now + delay)
        state.last_reason = "transient_error"
        return
    if code == "OutOfHostCapacity" or "capacity" in text:
        state.last_reason = "capacity"
        return
    state.last_reason = "other_failure"


def record_success(account_id: int) -> None:
    config = _ensure_config_loaded()
    if not config.enabled:
        return
    state = _state(account_id)
    state.total_success += 1
    state.success_streak = min(state.success_streak + 1, 100)
    if state.success_streak < config.success_recovery_threshold:
        return
    state.failure_streak = max(0, state.failure_streak - 1)
    state.rate_limit_streak = max(0, state.rate_limit_streak - 1)
    state.penalty_seconds *= 0.5
    if state.penalty_seconds < config.backoff_base:
        state.penalty_seconds = 0.0
    state.defer_until_monotonic = min(state.defer_until_monotonic, time.monotonic())
    state.success_streak = 0
    state.last_reason = "success_recovery"


def adaptive_retry_delay(account_id: int, base_seconds: int) -> int:
    base = max(1, int(base_seconds))
    config = _ensure_config_loaded()
    if not config.enabled:
        return base
    state = _state(account_id)
    remaining = max(0.0, state.defer_until_monotonic - time.monotonic())
    cadence_base = float(base) * _tenant_cadence_factor(account_id)
    return max(base, int(math.ceil(max(cadence_base, state.penalty_seconds, remaining))))


def scheduler_snapshot(account_id: int) -> dict[str, Any]:
    state = _state(account_id)
    data = asdict(state)
    data["defer_remaining_seconds"] = round(max(0.0, state.defer_until_monotonic - time.monotonic()), 3)
    data["cadence_factor"] = round(_tenant_cadence_factor(account_id), 3)
    return data


def _account_rows() -> list[dict[str, Any]]:
    with database() as connection:
        columns = {str(row["name"] if hasattr(row, "keys") else row[1]) for row in connection.execute("PRAGMA table_info(oci_accounts)").fetchall()}
        def col(name: str, fallback: str = "NULL") -> str:
            return name if name in columns else f"{fallback} AS {name}"
        sql = f"""
            SELECT id, custom_name, {col('email')}, {col('proxy_enabled', '0')},
                   {col('proxy_profile_id')}, {col('proxy_label')}, {col('proxy_last_ip')}
            FROM oci_accounts ORDER BY id
        """
        return [dict(row) for row in connection.execute(sql).fetchall()]


def scheduler_status() -> dict[str, Any]:
    config = _ensure_config_loaded()
    accounts = _account_rows()
    account_ids = {int(item["id"]) for item in accounts}
    profile_usage: dict[int, list[int]] = {}
    enriched = []
    for account in accounts:
        account_id = int(account["id"])
        state = scheduler_snapshot(account_id) if account_id in _states else asdict(TenantState()) | {"defer_remaining_seconds": 0.0}
        profile_id = account.get("proxy_profile_id")
        if account.get("proxy_enabled") and profile_id is not None:
            try:
                profile_usage.setdefault(int(profile_id), []).append(account_id)
            except (TypeError, ValueError):
                pass
        enriched.append({
            "account_id": account_id, "custom_name": account.get("custom_name"), "email": account.get("email"),
            "proxy_enabled": bool(account.get("proxy_enabled")), "proxy_profile_id": profile_id,
            "proxy_label": account.get("proxy_label"), "proxy_last_ip": account.get("proxy_last_ip"), "runtime": state,
        })
    shared = [{"proxy_profile_id": pid, "account_ids": ids, "count": len(ids)} for pid, ids in sorted(profile_usage.items()) if len(ids) > 1]
    real_states = {k: v for k, v in _states.items() if k in account_ids}
    throttled = sum(1 for s in real_states.values() if s.defer_until_monotonic > time.monotonic() and s.last_reason in {"rate_limit", "transient_error"})
    proxy_paused = sum(1 for s in real_states.values() if s.defer_until_monotonic > time.monotonic() and s.last_reason == "proxy_pause")
    return {
        "enabled": config.enabled,
        "settings": asdict(config),
        "summary": {
            "total_accounts": len(accounts),
            "proxy_bound_accounts": sum(1 for item in accounts if bool(item.get("proxy_enabled"))),
            "direct_accounts": sum(1 for item in accounts if not bool(item.get("proxy_enabled"))),
            "shared_proxy_groups": len(shared),
            "runtime_tenants": len(real_states),
            "throttled_tenants": throttled,
            "proxy_paused_tenants": proxy_paused,
            "total_429": sum(s.total_429 for s in real_states.values()),
            "total_failures": sum(s.total_failure for s in real_states.values()),
            "total_success": sum(s.total_success for s in real_states.values()),
        },
        "shared_proxies": shared,
        "accounts": enriched,
        "runtime_persistence": "memory",
        "configuration_persistence": "sqlite",
    }
