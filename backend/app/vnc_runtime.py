from __future__ import annotations

import asyncio
import os
import re
import shlex
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .config import settings
from .oci_advanced_service import create_console_connection, delete_console_connection
from .rc_repository import (
    create_vnc_session_record,
    get_vnc_session,
    get_vnc_session_by_token,
    update_vnc_session,
)


@dataclass(slots=True)
class RuntimeSession:
    session_id: int
    process: asyncio.subprocess.Process
    key_path: Path
    local_port: int
    connection_id: str
    account_id: int
    region: str
    expires_at: datetime
    timeout_task: asyncio.Task | None = None


_runtime: dict[int, RuntimeSession] = {}
_runtime_lock = asyncio.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _generate_keypair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode()
    return private_pem, public_key


def _replace_forward_port(args: list[str], local_port: int) -> list[str]:
    result = list(args)
    found = False
    index = 0
    while index < len(result):
        token = result[index]
        if token == "-L" and index + 1 < len(result):
            spec = result[index + 1]
            result[index + 1] = _rewrite_forward_spec(spec, local_port)
            found = True
            index += 2
            continue
        if token.startswith("-L") and len(token) > 2:
            result[index] = "-L" + _rewrite_forward_spec(token[2:], local_port)
            found = True
        index += 1
    if not found:
        raise ValueError("OCI VNC 连接字符串中没有本地端口转发参数")
    return result


def _rewrite_forward_spec(spec: str, local_port: int) -> str:
    value = str(spec).strip()
    # Supported SSH forms:
    #   5900:target:5900
    #   localhost:5900:target:5900
    #   127.0.0.1:5900:target:5900
    parts = value.split(":")
    if len(parts) < 3:
        raise ValueError("OCI VNC 本地端口转发格式无法识别")
    if len(parts) >= 4 and parts[0] in {"localhost", "127.0.0.1", "[::1]"}:
        parts[1] = str(local_port)
        return ":".join(parts)
    parts[0] = str(local_port)
    return ":".join(parts)


def _safe_ssh_args(connection_string: str, key_path: Path, local_port: int) -> list[str]:
    if not connection_string or len(connection_string) > 20000:
        raise ValueError("OCI 没有返回可用的 VNC 连接字符串")
    args = shlex.split(connection_string)
    if not args or Path(args[0]).name != "ssh":
        raise ValueError("OCI VNC 连接字符串不是 SSH 命令")
    dangerous = {";", "&&", "||", "|", ">", "<", "`"}
    if any(token in dangerous for token in args):
        raise ValueError("OCI VNC 连接字符串包含不安全字符")
    args = _replace_forward_port(args, local_port)
    options = [
        "-i",
        str(key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=20",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
    ]
    return [args[0], *options, *args[1:]]


async def _wait_for_port(port: int, process: asyncio.subprocess.Process, timeout: int = 45) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if process.returncode is not None:
            stderr = await process.stderr.read() if process.stderr else b""
            raise RuntimeError(
                "VNC SSH 隧道提前退出：" + stderr.decode(errors="replace")[-2000:]
            )
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port),
                timeout=1,
            )
            writer.close()
            await writer.wait_closed()
            return
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(1)
    raise TimeoutError("VNC SSH 隧道在 45 秒内未就绪")


async def _expire_session(session_id: int, expires_at: datetime) -> None:
    delay = max(0.0, (expires_at - _utc_now()).total_seconds())
    await asyncio.sleep(delay)
    await stop_vnc_session(session_id, reason="会话已到期")


async def start_vnc_session(
    *,
    account_id: int,
    instance_id: str,
    region: str,
    duration_minutes: int = 30,
) -> dict:
    if duration_minutes < 5 or duration_minutes > 120:
        raise ValueError("VNC 会话时长必须是 5–120 分钟")
    private_pem, public_key = _generate_keypair()
    connection = create_console_connection(
        account_id,
        instance_id,
        region=region,
        public_key=public_key,
    )
    connection_id = str(connection.get("id") or "")
    connection_string = str(connection.get("vnc_connection_string") or "")
    if not connection_id or not connection_string:
        if connection_id:
            try:
                delete_console_connection(account_id, connection_id, region)
            except Exception:
                pass
        raise RuntimeError("OCI 控制台连接未返回 VNC 连接字符串")

    local_port = _find_free_port()
    expires_at = _utc_now() + timedelta(minutes=duration_minutes)
    record, token = create_vnc_session_record(
        account_id=account_id,
        instance_id=instance_id,
        region=region,
        connection_id=connection_id,
        local_port=local_port,
        expires_at=expires_at.isoformat(),
    )
    session_id = int(record["id"])
    session_dir = settings.data_dir / "vnc" / str(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    key_path = session_dir / "id_rsa"
    key_path.write_text(private_pem)
    os.chmod(key_path, 0o600)
    args = _safe_ssh_args(connection_string, key_path, local_port)

    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    runtime = RuntimeSession(
        session_id=session_id,
        process=process,
        key_path=key_path,
        local_port=local_port,
        connection_id=connection_id,
        account_id=account_id,
        region=region,
        expires_at=expires_at,
    )
    async with _runtime_lock:
        _runtime[session_id] = runtime
    try:
        await _wait_for_port(local_port, process)
    except Exception as exc:
        update_vnc_session(
            session_id,
            status="FAILED",
            error=f"{type(exc).__name__}: {exc}",
            stopped_at=_utc_now().isoformat(),
        )
        await _cleanup_runtime(runtime, delete_oci_connection=True)
        raise

    update_vnc_session(session_id, status="ACTIVE")
    runtime.timeout_task = asyncio.create_task(_expire_session(session_id, expires_at))
    return {
        "id": session_id,
        "account_id": account_id,
        "instance_id": instance_id,
        "region": region,
        "status": "ACTIVE",
        "expires_at": expires_at.isoformat(),
        "token": token,
        "viewer_url": f"/vnc.html?token={token}",
    }


async def _cleanup_runtime(runtime: RuntimeSession, *, delete_oci_connection: bool) -> None:
    if runtime.timeout_task and runtime.timeout_task is not asyncio.current_task():
        runtime.timeout_task.cancel()
    if runtime.process.returncode is None:
        runtime.process.terminate()
        try:
            await asyncio.wait_for(runtime.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            runtime.process.kill()
            await runtime.process.wait()
    try:
        runtime.key_path.unlink(missing_ok=True)
        runtime.key_path.parent.rmdir()
    except OSError:
        pass
    if delete_oci_connection:
        try:
            await asyncio.to_thread(
                delete_console_connection,
                runtime.account_id,
                runtime.connection_id,
                runtime.region,
            )
        except Exception:
            pass
    async with _runtime_lock:
        _runtime.pop(runtime.session_id, None)


async def stop_vnc_session(session_id: int, *, reason: str = "用户停止") -> bool:
    async with _runtime_lock:
        runtime = _runtime.get(int(session_id))
    if runtime:
        await _cleanup_runtime(runtime, delete_oci_connection=True)
        update_vnc_session(
            int(session_id),
            status="STOPPED",
            error=reason,
            stopped_at=_utc_now().isoformat(),
        )
        return True
    record = get_vnc_session(int(session_id))
    if not record:
        return False
    if record["status"] in {"ACTIVE", "STARTING"}:
        update_vnc_session(
            int(session_id),
            status="INTERRUPTED",
            error="运行时隧道不存在，已清理数据库状态",
            stopped_at=_utc_now().isoformat(),
        )
    return True


async def stop_all_vnc_sessions() -> None:
    async with _runtime_lock:
        ids = list(_runtime)
    for session_id in ids:
        await stop_vnc_session(session_id, reason="服务停止")


def resolve_vnc_target(token: str) -> tuple[dict, int]:
    record = get_vnc_session_by_token(token)
    if not record:
        raise KeyError("VNC 会话不存在或已过期")
    if record["status"] != "ACTIVE":
        raise RuntimeError(f"VNC 会话当前状态：{record['status']}")
    runtime = _runtime.get(int(record["id"]))
    if not runtime or runtime.process.returncode is not None:
        raise RuntimeError("VNC SSH 隧道未运行")
    return record, int(runtime.local_port)
