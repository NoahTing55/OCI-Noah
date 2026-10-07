from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, TypeVar

from fastapi import HTTPException

T = TypeVar("T")

ERROR_LABELS = {
    "RATE_LIMIT": "OCI 限流",
    "AUTHENTICATION": "认证失败",
    "PERMISSION": "权限不足",
    "CAPACITY": "容量或配额不足",
    "CONFIGURATION": "资源配置错误",
    "NETWORK": "网络或代理异常",
    "SERVICE": "OCI 服务异常",
    "CONFLICT": "资源状态冲突",
    "NOT_FOUND": "资源不存在",
    "UNKNOWN": "未分类错误",
}


@dataclass(frozen=True)
class OciErrorInfo:
    category: str
    label: str
    retryable: bool
    status_code: int | None
    service_code: str | None
    message: str


class ResilientOperationError(RuntimeError):
    def __init__(self, original: Exception, info: OciErrorInfo, attempts: int):
        super().__init__(info.message)
        self.original = original
        self.info = info
        self.attempts = attempts


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return str(detail if isinstance(detail, str) else detail or exc)
    message = getattr(exc, "message", None)
    if message:
        return str(message)
    return str(exc) or type(exc).__name__


def classify_oci_error(exc: Exception) -> OciErrorInfo:
    status = getattr(exc, "status", None)
    if isinstance(exc, HTTPException):
        status = exc.status_code
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    service_code = str(getattr(exc, "code", "") or "") or None
    text = _error_text(exc)
    haystack = f"{type(exc).__name__} {service_code or ''} {text}".lower()

    category = "UNKNOWN"
    retryable = False
    if status_code == 429 or any(token in haystack for token in (
        "too many requests", "toomanyrequests", "rate limit", "throttl",
    )):
        category, retryable = "RATE_LIMIT", True
    elif status_code == 401 or any(token in haystack for token in (
        "notauthenticated", "not authenticated", "invalid signature", "signature mismatch",
        "fingerprint", "private key", "authentication failed",
    )):
        category = "AUTHENTICATION"
    elif status_code == 403 or any(token in haystack for token in (
        "notauthorized", "not authorized", "forbidden", "permission denied", "policy",
    )):
        category = "PERMISSION"
    elif status_code == 404 or any(token in haystack for token in (
        "notfound", "not found", "does not exist",
    )):
        category = "NOT_FOUND"
    elif any(token in haystack for token in (
        "out of host capacity", "outofhostcapacity", "limitexceeded", "quota exceeded",
        "capacity", "service limit",
    )):
        category = "CAPACITY"
    elif status_code == 409 or any(token in haystack for token in (
        "incorrectstate", "incorrect state", "conflict", "currently in state", "being modified",
    )):
        category, retryable = "CONFLICT", True
    elif status_code is not None and status_code >= 500:
        category, retryable = "SERVICE", True
    elif any(token in haystack for token in (
        "timeout", "timed out", "connection", "proxyerror", "proxy error", "dns",
        "name resolution", "network is unreachable", "connection reset", "remote disconnected",
        "sslerror", "tls",
    )):
        category, retryable = "NETWORK", True
    elif status_code == 400 or any(token in haystack for token in (
        "invalidparameter", "invalid parameter", "invalid shape", "invalid image", "subnet",
        "availability domain", "region is not subscribed", "bad request",
    )):
        category = "CONFIGURATION"

    return OciErrorInfo(
        category=category,
        label=ERROR_LABELS[category],
        retryable=retryable,
        status_code=status_code,
        service_code=service_code,
        message=text[:4000],
    )


def retry_delay_seconds(info: OciErrorInfo, attempt: int) -> int:
    attempt = max(1, int(attempt))
    base = {
        "RATE_LIMIT": 5,
        "SERVICE": 3,
        "NETWORK": 2,
        "CONFLICT": 2,
    }.get(info.category, 2)
    ceiling = 60 if info.category == "RATE_LIMIT" else 30
    raw = min(ceiling, base * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0, min(2.0, raw * 0.15))
    return max(1, int(round(raw + jitter)))


async def adaptive_oci_call(
    operation: Callable[[], T],
    *,
    max_attempts: int,
    before_attempt: Callable[[int], None] | None = None,
    on_retry: Callable[[OciErrorInfo, int, int, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[T, int]:
    attempts = max(1, int(max_attempts))
    last_error: Exception | None = None
    last_info: OciErrorInfo | None = None
    for attempt in range(1, attempts + 1):
        if cancel_requested and cancel_requested():
            raise asyncio.CancelledError
        if before_attempt:
            before_attempt(attempt)
        try:
            result = await asyncio.to_thread(operation)
            return result, attempt
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - classification is the purpose here.
            last_error = exc
            last_info = classify_oci_error(exc)
            if not last_info.retryable or attempt >= attempts:
                raise ResilientOperationError(exc, last_info, attempt) from exc
            delay = retry_delay_seconds(last_info, attempt)
            next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            if on_retry:
                on_retry(last_info, attempt, delay, next_at)
            for _ in range(delay):
                if cancel_requested and cancel_requested():
                    raise asyncio.CancelledError
                await asyncio.sleep(1)
    assert last_error is not None and last_info is not None
    raise ResilientOperationError(last_error, last_info, attempts)
