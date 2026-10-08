import asyncio
import itertools
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

from .database import add_audit_log
from .credential_crypto import decrypt_secret, encrypt_secret
from .launch_repository import (
    add_launch_attempt,
    create_launch_job,
    finish_launch_job,
    get_launch_job,
    launch_cancel_requested,
    request_launch_cancel,
    save_launch_progress,
    set_launch_waiting,
    start_launch_job,
)
from .launch_service import (
    display_name_for_sequence,
    launch_error,
    launch_one_instance,
    validate_launch_request,
)
from .telegram_service import send_configured_telegram
from .adaptive_launch_scheduler import (
    adaptive_initial_stagger,
    adaptive_launch_call,
    adaptive_retry_delay,
)

_tasks: dict[int, asyncio.Task] = {}



# OCI-N&T V1.0.4 1.0.4-launch-key-list1: detailed launch success notification
_notification_tasks: set[asyncio.Task] = set()


def _ssh_public_key_fingerprint(public_key: str | None) -> str | None:
    import base64
    import hashlib

    value = str(public_key or "").strip()
    parts = value.split()
    if len(parts) < 2:
        return None
    try:
        encoded = parts[1]
        encoded += "=" * ((4 - len(encoded) % 4) % 4)
        blob = base64.b64decode(encoded)
        digest = hashlib.sha256(blob).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
    except Exception:
        return None


async def _notify_launch_instance_success(
    *,
    job_id: int,
    account_id: int,
    sequence_no: int,
    request_payload: dict,
    result: dict,
) -> None:
    from .launch_service import resolve_instance_public_ip

    public_ip = None
    try:
        public_ip = await asyncio.to_thread(
            resolve_instance_public_ip,
            account_id,
            str(result.get("region") or request_payload.get("region") or ""),
            str(result.get("compartment_id") or request_payload.get("compartment_id") or ""),
            str(result.get("id") or ""),
        )
    except Exception:
        public_ip = None

    login_mode = str(
        request_payload.get("login_mode") or "SSH_KEY"
    ).upper()
    root_password = (
        _root_password_from_request(request_payload)
        if login_mode == "ROOT_PASSWORD"
        else None
    )
    fingerprint = _ssh_public_key_fingerprint(
        request_payload.get("ssh_public_key")
    )
    display_name = str(
        result.get("display_name")
        or request_payload.get("display_name")
        or f"N&T-{sequence_no}"
    )
    shape = str(
        result.get("shape")
        or request_payload.get("shape")
        or "—"
    )
    region = str(
        result.get("region")
        or request_payload.get("region")
        or "—"
    )

    lines = [
        "✅ OCI-N&T 开机成功",
        "",
        f"租户 ID：{account_id}",
        f"实例：{display_name}",
        f"区域：{region}",
        f"架构：{request_payload.get('architecture') or '—'}",
        f"配置：{shape}",
        f"公网 IP：{public_ip or '暂未读取到，可稍后在实例管理查看'}",
        f"登录方式：{'root + 密码' if login_mode == 'ROOT_PASSWORD' else 'ubuntu + SSH 密钥'}",
        f"用户：{'root' if login_mode == 'ROOT_PASSWORD' else 'ubuntu'}",
        "SSH 端口：22",
    ]

    if login_mode == "ROOT_PASSWORD":
        lines.append(f"密码：{root_password or '密码解密失败，请使用 VNC 重置'}")
        if public_ip:
            lines.append(f"登录：ssh root@{public_ip}")
        lines.append("安全提示：请登录后尽快修改 root 密码。")
    else:
        if fingerprint:
            lines.append(f"密钥指纹：{fingerprint}")
        if public_ip:
            lines.append(
                f"登录：ssh -i <创建配置时下载的私钥.pem> ubuntu@{public_ip}"
            )
        lines.append(
            "私钥仅保存在你的下载文件中，OCI-N&T 不保存也不发送私钥。"
        )

    try:
        await asyncio.to_thread(
            send_configured_telegram,
            "\n".join(lines),
            "launch_success",
        )
    except Exception:
        # Telegram availability must not affect OCI launch success.
        return


def _spawn_launch_success_notification(
    *,
    job_id: int,
    account_id: int,
    sequence_no: int,
    request_payload: dict,
    result: dict,
) -> None:
    task = asyncio.create_task(
        _notify_launch_instance_success(
            job_id=job_id,
            account_id=account_id,
            sequence_no=sequence_no,
            request_payload=request_payload,
            result=result,
        )
    )
    _notification_tasks.add(task)
    task.add_done_callback(_notification_tasks.discard)



def _generate_root_password(length: int = 20) -> str:
    # 避免 shell、YAML、Telegram 中容易产生歧义的字符。
    alphabet = string.ascii_letters + string.digits + "@#%+=_-"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(char.islower() for char in value)
            and any(char.isupper() for char in value)
            and any(char.isdigit() for char in value)
            and any(char in "@#%+=_-" for char in value)
        ):
            return value


def _root_password_from_request(request_payload: dict) -> str | None:
    encrypted = str(
        request_payload.get("root_password_encrypted") or ""
    ).strip()
    if not encrypted:
        return None
    try:
        return decrypt_secret(encrypted)
    except Exception:
        return None


class LaunchBusyError(RuntimeError):
    pass


async def start_launch_task(
    *,
    account_id: int,
    requested_by: str,
    ip_address: str | None,
    mode: str,
    request_payload: dict,
    requested_count: int,
    max_attempts: int,
    retry_interval_seconds: int,
    concurrency: int,
    trusted_saved_request: bool = False,
) -> dict:
    from .maintenance_state import reject_new_work_during_maintenance
    reject_new_work_during_maintenance()
    mode = str(mode or "CREATE").upper()
    if mode not in {"CREATE", "CAPACITY_RETRY"}:
        raise ValueError("任务模式只允许 CREATE 或 CAPACITY_RETRY")
    requested_count = int(requested_count)
    max_attempts = int(max_attempts)
    retry_interval_seconds = int(retry_interval_seconds)
    concurrency = int(concurrency)
    if not 1 <= requested_count <= 20:
        raise ValueError("创建数量必须在 1 到 20 之间")
    if not 0 <= max_attempts <= 10000:
        raise ValueError("最大尝试次数必须在 0 到 10000 之间；0 表示直到成功")
    if mode == "CREATE":
        max_attempts = 1
    if not 15 <= retry_interval_seconds <= 86400:
        raise ValueError("抢机重试间隔必须在 15 到 86400 秒之间")
    if not 1 <= concurrency <= 5:
        raise ValueError("并发数必须在 1 到 5 之间")

    normalized = validate_launch_request(
        account_id,
        request_payload,
        allow_expired=trusted_saved_request,
    )

    login_mode = str(
        normalized.get("login_mode") or "SSH_KEY"
    ).strip().upper()
    if login_mode not in {"SSH_KEY", "ROOT_PASSWORD"}:
        raise ValueError("登录方式只允许 SSH_KEY 或 ROOT_PASSWORD")

    normalized["login_mode"] = login_mode

    if login_mode == "ROOT_PASSWORD":
        plain_password = str(
            normalized.pop("root_password", "") or ""
        ).strip()

        if not plain_password and not normalized.get("root_password_encrypted"):
            plain_password = _generate_root_password()

        if plain_password:
            if len(plain_password) < 12:
                raise ValueError("root 密码至少需要 12 个字符")
            if len(plain_password) > 128:
                raise ValueError("root 密码不能超过 128 个字符")
            if any(char in plain_password for char in "\r\n:"):
                raise ValueError("root 密码不能包含换行符或冒号")
            normalized["root_password_encrypted"] = encrypt_secret(
                plain_password
            )

        normalized["ssh_public_key"] = None
    else:
        normalized.pop("root_password", None)
        normalized.pop("root_password_encrypted", None)

    try:
        job = create_launch_job(
            account_id=account_id,
            requested_by=requested_by,
            mode=mode,
            request_payload=normalized,
            requested_count=requested_count,
            max_attempts=max_attempts,
            retry_interval_seconds=retry_interval_seconds,
            concurrency=concurrency,
        )
    except RuntimeError as exc:
        raise LaunchBusyError(str(exc)) from exc

    job_id = int(job["id"])
    task = asyncio.create_task(
        _run_launch_task(
            job_id=job_id,
            account_id=account_id,
            requested_by=requested_by,
            ip_address=ip_address,
            request_payload=normalized,
            requested_count=requested_count,
            max_attempts=max_attempts,
            retry_interval_seconds=retry_interval_seconds,
            concurrency=concurrency,
        )
    )
    _tasks[job_id] = task
    task.add_done_callback(lambda _: _tasks.pop(job_id, None))
    return get_launch_job(job_id) or job


async def _run_launch_task(
    *,
    job_id: int,
    account_id: int,
    requested_by: str,
    ip_address: str | None,
    request_payload: dict,
    requested_count: int,
    max_attempts: int,
    retry_interval_seconds: int,
    concurrency: int,
) -> None:
    successful: dict[int, dict] = {}
    permanent_failures: set[int] = set()
    total_failures = 0
    last_error: str | None = None
    retry_tokens = {
        sequence: str(uuid.uuid4())
        for sequence in range(1, requested_count + 1)
    }
    try:
        start_launch_job(job_id)

        start_message = (
            "🚀 OCI-N&T 开机任务已启动\n\n"
            f"租户 ID：{account_id}\n"
            f"实例名称：{request_payload.get('display_name') or 'N&T'}\n"
            f"区域：{request_payload.get('region') or '—'}\n"
            f"架构：{request_payload.get('architecture') or '—'}\n"
            f"配置：{request_payload.get('shape') or '—'}\n"
            f"目标数量：{requested_count}\n"
            f"并发数：{concurrency}\n"
            f"运行间隔：{retry_interval_seconds} 秒\n"
            "运行策略：持续尝试，成功或人工停止后结束"
        )
        try:
            await asyncio.to_thread(
                send_configured_telegram,
                start_message,
                "launch_task",
            )
        except Exception:
            # Telegram 异常不能影响 OCI 开机任务。
            pass

        # OCI-N&T V1.0.4 1.0.4-adaptive-launch-c1
        await adaptive_initial_stagger(account_id, job_id)
        rounds = itertools.count(1) if max_attempts == 0 else range(1, max_attempts + 1)
        for round_no in rounds:
            if launch_cancel_requested(job_id):
                finish_launch_job(
                    job_id,
                    status="CANCELLED",
                    success_count=len(successful),
                    failure_count=total_failures,
                    last_error=last_error,
                )
                _audit(
                    requested_by,
                    ip_address,
                    "OCI_LAUNCH_CANCELLED",
                    job_id,
                    account_id,
                    len(successful),
                    total_failures,
                )
                return

            pending = [
                sequence
                for sequence in range(1, requested_count + 1)
                if sequence not in successful and sequence not in permanent_failures
            ]
            if not pending:
                break

            semaphore = asyncio.Semaphore(concurrency)

            async def one(sequence_no: int) -> tuple[int, dict | None, str | None, bool]:
                async with semaphore:
                    logical_request = {
                        **request_payload,
                        "_opc_retry_token": retry_tokens[sequence_no],
                    }
                    # CREATE remains a single task round, but OCI 429 is given
                    # two bounded, cancellable retries. All three calls reuse
                    # the same opc-retry-token for idempotency.
                    for rate_attempt in range(1, 4):
                        if launch_cancel_requested(job_id):
                            return sequence_no, None, "任务已取消", False
                        try:
                            result = await adaptive_launch_call(
                                launch_one_instance,
                                account_id,
                                logical_request,
                                sequence_no=sequence_no,
                                requested_count=requested_count,
                                trusted_job_request=True,
                            )
                            return sequence_no, result, None, False
                        except Exception as exc:  # OCI exceptions are normalized here.
                            error, retryable = launch_error(exc)
                            is_rate_limit = (
                                "429" in error
                                or "TooManyRequests" in error
                                or "too many requests" in error.lower()
                            )
                            if not is_rate_limit or rate_attempt >= 3:
                                return sequence_no, None, error, retryable
                            wait_seconds = adaptive_retry_delay(
                                account_id,
                                max(15, retry_interval_seconds),
                            )
                            for _ in range(wait_seconds):
                                if launch_cancel_requested(job_id):
                                    return sequence_no, None, "任务已取消", False
                                await asyncio.sleep(1)
                    return sequence_no, None, "429 重试已结束", True

            results = await asyncio.gather(*(one(sequence) for sequence in pending))
            round_has_retryable_failure = False
            for sequence_no, result, error, retryable in results:
                display_name = display_name_for_sequence(
                    request_payload.get("display_name") or "N&T",
                    requested_count,
                    sequence_no,
                )
                if result:
                    successful[sequence_no] = result
                    _spawn_launch_success_notification(
                        job_id=job_id,
                        account_id=account_id,
                        sequence_no=sequence_no,
                        request_payload=request_payload,
                        result=result,
                    )
                    add_launch_attempt(
                        job_id=job_id,
                        round_no=round_no,
                        sequence_no=sequence_no,
                        status="SUCCESS",
                        display_name=display_name,
                        instance_id=result.get("id"),
                        lifecycle_state=result.get("lifecycle_state"),
                    )
                else:
                    total_failures += 1
                    last_error = error
                    round_has_retryable_failure = round_has_retryable_failure or retryable
                    if not retryable:
                        permanent_failures.add(sequence_no)
                    add_launch_attempt(
                        job_id=job_id,
                        round_no=round_no,
                        sequence_no=sequence_no,
                        status="FAILED",
                        display_name=display_name,
                        error=error,
                        retryable=retryable,
                    )

            save_launch_progress(
                job_id,
                current_attempt=round_no,
                success_count=len(successful),
                failure_count=total_failures,
                last_error=last_error,
            )

            if len(successful) >= requested_count:
                break

            continuous = max_attempts == 0

            # 持续开机任务遇到 OCI 临时错误必须继续。
            # OutOfHostCapacity、InternalError、429、5xx 均由 launch_error
            # 标记为 retryable，不能进入任务结束流程。
            if not continuous:
                if permanent_failures and not round_has_retryable_failure:
                    break
                if round_no >= max_attempts:
                    break
            elif permanent_failures and not round_has_retryable_failure:
                # 鉴权失败、参数错误等永久错误停止，避免无限无效请求。
                break

            adaptive_wait_seconds = adaptive_retry_delay(
                account_id,
                retry_interval_seconds,
            )
            next_at = datetime.now(timezone.utc) + timedelta(
                seconds=adaptive_wait_seconds
            )
            set_launch_waiting(job_id, next_at.isoformat())
            for _ in range(adaptive_wait_seconds):
                if launch_cancel_requested(job_id):
                    finish_launch_job(
                        job_id,
                        status="CANCELLED",
                        success_count=len(successful),
                        failure_count=total_failures,
                        last_error=last_error,
                    )
                    _audit(
                        requested_by,
                        ip_address,
                        "OCI_LAUNCH_CANCELLED",
                        job_id,
                        account_id,
                        len(successful),
                        total_failures,
                    )
                    return
                await asyncio.sleep(1)

        completed = len(successful) >= requested_count
        status = "COMPLETED" if completed else "FAILED"
        finish_launch_job(
            job_id,
            status=status,
            success_count=len(successful),
            failure_count=total_failures,
            last_error=None if completed else last_error or "未达到目标创建数量",
        )
        message = (
            f"{'✅' if completed else '⛔'} OCI-N&T 开机任务{'成功' if completed else '结束'}\n\n"
            f"租户 ID：{account_id}\n"
            f"实例名称：{request_payload.get('display_name') or 'N&T'}\n"
            f"架构：{request_payload.get('architecture') or '—'}\n"
            f"配置：{request_payload.get('shape') or '—'}\n"
            f"区域：{request_payload.get('region') or '—'}\n"
            f"成功：{len(successful)}/{requested_count}\n"
            f"失败尝试：{total_failures}\n"
            f"运行间隔：{retry_interval_seconds} 秒"
        )
        if last_error and not completed:
            message += f"\n最后错误：{last_error[:500]}"
        await asyncio.to_thread(send_configured_telegram, message, "launch_task")
        _audit(
            requested_by,
            ip_address,
            "OCI_LAUNCH_COMPLETED" if completed else "OCI_LAUNCH_FAILED",
            job_id,
            account_id,
            len(successful),
            total_failures,
        )
    except asyncio.CancelledError:
        finish_launch_job(
            job_id,
            status="INTERRUPTED",
            success_count=len(successful),
            failure_count=total_failures,
            last_error="任务进程终止；系统不会自动恢复 OCI 查询",
        )
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        finish_launch_job(
            job_id,
            status="FAILED",
            success_count=len(successful),
            failure_count=total_failures,
            last_error=error,
        )
        _audit(
            requested_by,
            ip_address,
            "OCI_LAUNCH_FAILED",
            job_id,
            account_id,
            len(successful),
            total_failures,
            error,
        )


def cancel_launch_task(job_id: int, account_id: int) -> bool:
    return request_launch_cancel(job_id, account_id)


def _audit(
    username: str,
    ip_address: str | None,
    action: str,
    job_id: int,
    account_id: int,
    success_count: int,
    failure_count: int,
    error: str | None = None,
) -> None:
    detail = (
        f"job={job_id},account={account_id},success={success_count},"
        f"failures={failure_count}"
    )
    if error:
        detail += f",error={error[:1000]}"
    add_audit_log(
        username,
        action,
        ip_address,
        detail,
        "OCI_LAUNCH_JOB",
        str(job_id),
    )
