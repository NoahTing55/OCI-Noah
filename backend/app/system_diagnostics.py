import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from .backup_service import list_backups
from .backup_schedule_service import schedule_status
from .config import settings
from .release_service import current_release_info
from .settings_repository import load_backup_policy, load_google_auth_settings, load_telegram_settings
from .system_monitor_service import collect_container_status, collect_metric, load_monitor_settings, traffic_totals

_PROCESS_STARTED_AT = datetime.now(timezone.utc)
_PROCESS_STARTED_MONOTONIC = time.monotonic()

_COUNT_TABLES = {
    "accounts": "oci_accounts",
    "instances": "oci_instance_cache",
    "proxies": "proxy_profiles",
    "cloudflare_accounts": "cloudflare_accounts",
    "audit_logs": "audit_logs",
    "launch_profiles": "launch_profiles",
    "launch_jobs": "launch_jobs",
    "manual_tasks": "manual_tasks",
}


def _component(key: str, name: str, status: str, summary: str, detail: str = "") -> dict:
    return {
        "key": key,
        "name": name,
        "status": status,
        "summary": summary,
        "detail": detail,
    }


def _safe_table_count(connection: sqlite3.Connection, table_name: str) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if not exists:
        return 0
    row = connection.execute(f'SELECT COUNT(*) AS total FROM "{table_name}"').fetchone()
    return int(row[0] if row else 0)


def _directory_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


def collect_system_diagnostics() -> dict:
    now = datetime.now(timezone.utc)
    components: list[dict] = []
    counts = {key: 0 for key in _COUNT_TABLES}
    database_size = 0
    database_quick_check = "未检查"
    database_writable = False
    task_state = {"active": 0, "interrupted": 0}
    backup_schedule = {}
    release = {}

    try:
        database_size = settings.db_path.stat().st_size if settings.db_path.exists() else 0
        connection = sqlite3.connect(settings.db_path, timeout=5)
        try:
            quick_row = connection.execute("PRAGMA quick_check").fetchone()
            database_quick_check = str(quick_row[0] if quick_row else "未知")
            for key, table_name in _COUNT_TABLES.items():
                counts[key] = _safe_table_count(connection, table_name)
            if _safe_table_count(connection, "manual_tasks"):
                row = connection.execute(
                    """
                    SELECT
                        SUM(CASE WHEN status IN ('PENDING','RUNNING','WAITING','CANCELLING') THEN 1 ELSE 0 END) AS active,
                        SUM(CASE WHEN status = 'INTERRUPTED' THEN 1 ELSE 0 END) AS interrupted
                    FROM manual_tasks
                    """
                ).fetchone()
                task_state = {
                    "active": int(row[0] or 0),
                    "interrupted": int(row[1] or 0),
                }
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
            database_writable = True
        finally:
            connection.close()
        if database_quick_check.lower() == "ok" and database_writable:
            components.append(_component(
                "database",
                "SQLite 数据库",
                "ok",
                "完整且可写",
                f"quick_check={database_quick_check} · {database_size} bytes",
            ))
        else:
            components.append(_component(
                "database",
                "SQLite 数据库",
                "error",
                "数据库检查异常",
                f"quick_check={database_quick_check} · 可写={database_writable}",
            ))
    except Exception as exc:
        components.append(_component(
            "database",
            "SQLite 数据库",
            "error",
            "数据库无法正常访问",
            f"{type(exc).__name__}: {exc}",
        ))

    components.append(_component(
        "tasks",
        "手动任务中心",
        "warning" if task_state["interrupted"] else "ok",
        (
            f"{task_state['interrupted']} 条任务需要重试"
            if task_state["interrupted"]
            else (f"{task_state['active']} 条任务正在运行" if task_state["active"] else "没有遗留运行任务")
        ),
        "服务重启后的未完成项目会标记为已中断，可在任务中心只重试中断项。",
    ))

    try:
        Fernet(settings.credential_encryption_key.encode("utf-8"))
        components.append(_component(
            "encryption",
            "凭据加密",
            "ok",
            "加密密钥有效",
            "OCI 私钥、代理和第三方 Token 使用 Fernet 加密保存。",
        ))
    except Exception as exc:
        components.append(_component(
            "encryption",
            "凭据加密",
            "error",
            "加密密钥无效",
            f"{type(exc).__name__}: {exc}",
        ))

    backup_files: list[dict] = []
    backup_total_size = 0
    backup_writable = _directory_writable(settings.backup_dir)
    try:
        backup_files = list_backups()
        backup_total_size = sum(int(item.get("size") or 0) for item in backup_files)
    except Exception as exc:
        components.append(_component(
            "backups",
            "本地备份目录",
            "error",
            "备份目录无法读取",
            f"{type(exc).__name__}: {exc}",
        ))
    else:
        components.append(_component(
            "backups",
            "本地备份目录",
            "ok" if backup_writable else "error",
            "可正常创建备份" if backup_writable else "备份目录不可写",
            f"当前 {len(backup_files)} 份备份 · 共 {backup_total_size} bytes",
        ))

    storage = {
        "total_bytes": 0,
        "used_bytes": 0,
        "free_bytes": 0,
        "used_percent": 0.0,
        "database_bytes": database_size,
        "backup_bytes": backup_total_size,
    }
    try:
        usage = shutil.disk_usage(settings.data_dir)
        used_percent = round((usage.used / usage.total * 100) if usage.total else 0.0, 1)
        storage.update({
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
            "used_percent": used_percent,
        })
        free_ratio = (usage.free / usage.total) if usage.total else 0.0
        if free_ratio < 0.02:
            disk_status = "error"
            disk_summary = "磁盘空间严重不足"
        elif free_ratio < 0.10:
            disk_status = "warning"
            disk_summary = "磁盘空间偏低"
        else:
            disk_status = "ok"
            disk_summary = "磁盘空间充足"
        components.append(_component(
            "storage",
            "数据磁盘",
            disk_status,
            disk_summary,
            f"已使用 {used_percent}% · 剩余 {usage.free} bytes",
        ))
    except Exception as exc:
        components.append(_component(
            "storage",
            "数据磁盘",
            "warning",
            "无法读取磁盘空间",
            f"{type(exc).__name__}: {exc}",
        ))

    telegram = {"enabled": False, "configured": False}
    google = {"enabled": False, "configured": False}
    try:
        telegram_settings = load_telegram_settings(include_token=False)
        telegram = {
            "enabled": bool(telegram_settings.get("enabled")),
            "configured": bool(telegram_settings.get("chat_id") and telegram_settings.get("has_bot_token")),
        }
        components.append(_component(
            "telegram",
            "Telegram 通知",
            "ok" if (not telegram["enabled"] or telegram["configured"]) else "warning",
            "已启用并配置完成" if telegram["enabled"] and telegram["configured"] else ("已停用" if not telegram["enabled"] else "已启用但配置不完整"),
            "本项只读取本地设置，不发送测试消息。",
        ))
    except Exception as exc:
        components.append(_component(
            "telegram",
            "Telegram 通知",
            "warning",
            "无法读取通知配置",
            f"{type(exc).__name__}: {exc}",
        ))

    try:
        google_settings = load_google_auth_settings(include_secret=False)
        google = {
            "enabled": bool(google_settings.get("enabled")),
            "configured": bool(google_settings.get("configured")),
        }
        components.append(_component(
            "google",
            "Gmail 登录",
            "ok" if (not google["enabled"] or google["configured"]) else "warning",
            "已启用并配置完成" if google["enabled"] and google["configured"] else ("已停用" if not google["enabled"] else "已启用但配置不完整"),
            "本项只读取本地设置，不访问 Google。",
        ))
    except Exception as exc:
        components.append(_component(
            "google",
            "Gmail 登录",
            "warning",
            "无法读取登录配置",
            f"{type(exc).__name__}: {exc}",
        ))

    components.insert(0, _component(
        "api",
        "API 服务",
        "ok",
        "运行正常",
        f"{settings.app_name} {settings.display_version} · 进程已运行 {int(time.monotonic() - _PROCESS_STARTED_MONOTONIC)} 秒",
    ))
    try:
        backup_schedule = schedule_status()
        schedule_enabled = bool(backup_schedule.get("enabled"))
        schedule_failed = backup_schedule.get("last_status") == "FAILED"
        components.append(_component(
            "backup_schedule",
            "自动本地备份",
            "warning" if schedule_failed else "ok",
            (
                "最近一次自动备份失败"
                if schedule_failed
                else ("已启用" if schedule_enabled else "已关闭")
            ),
            (
                f"下次执行：{backup_schedule.get('next_run_at') or '—'} · "
                f"最近备份：{backup_schedule.get('last_backup_name') or '—'}"
            ),
        ))
    except Exception as exc:
        components.append(_component(
            "backup_schedule", "自动本地备份", "warning",
            "无法读取自动备份计划", f"{type(exc).__name__}: {exc}",
        ))

    try:
        release = current_release_info()
        components.append(_component(
            "release", "版本与升级记录", "ok",
            f"V{release.get('version')} · schema {release.get('schema_version')}",
            f"构建：{release.get('build_id')} · 指纹：{str(release.get('source_fingerprint') or '')[:16]}",
        ))
    except Exception as exc:
        components.append(_component(
            "release", "版本与升级记录", "warning",
            "无法读取版本记录", f"{type(exc).__name__}: {exc}",
        ))

    try:
        monitor_config = load_monitor_settings()
        metric = collect_metric(persist=False)
        traffic = traffic_totals()
        containers = collect_container_status()
        unhealthy = [
            item for item in containers.get("containers", [])
            if item.get("state") != "running" or item.get("health") not in {"healthy", "none"}
        ]
        components.append(_component(
            "monitor", "服务器运行监控",
            "warning" if unhealthy else "ok",
            (f"{len(unhealthy)} 个容器状态异常" if unhealthy else "实时与历史采集正常"),
            (
                f"CPU {metric.get('cpu_percent', 0):.1f}% · "
                f"网卡 {metric.get('interface') or '未识别'} · "
                f"本月流量 {int(traffic.get('month', {}).get('rx_bytes', 0)) + int(traffic.get('month', {}).get('tx_bytes', 0))} bytes · "
                f"历史保留 {monitor_config.get('retention_days', 30)} 天"
            ),
        ))
    except Exception as exc:
        components.append(_component(
            "monitor", "服务器运行监控", "warning",
            "无法读取监控状态", f"{type(exc).__name__}: {exc}",
        ))

    components.append(_component(
        "query_policy",
        "OCI 查询策略",
        "ok",
        "后台查询已关闭",
        "账户检测、实例同步和资源操作仅由手动操作触发。",
    ))

    statuses = [item["status"] for item in components]
    overall_status = "error" if "error" in statuses else ("warning" if "warning" in statuses else "ok")

    return {
        "checked_at": now.isoformat(),
        "service": settings.app_name,
        "version": settings.version,
        "display_version": settings.display_version,
        "started_at": _PROCESS_STARTED_AT.isoformat(),
        "uptime_seconds": int(time.monotonic() - _PROCESS_STARTED_MONOTONIC),
        "overall_status": overall_status,
        "counts": counts,
        "backups": {
            "count": len(backup_files),
            "total_bytes": backup_total_size,
            "writable": backup_writable,
        },
        "storage": storage,
        "tasks": task_state,
        "configuration": {
            "telegram": telegram,
            "google": google,
            "manual_query_only": True,
            "backup_schedule": backup_schedule,
            "release": release,
        },
        "components": components,
    }


_SUPPORT_URI_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/@\s:]+):([^/@\s]+)@")
_SUPPORT_BEARER = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]+")
_SUPPORT_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|api[_-]?key|secret|password|passphrase|authorization)=)[^&#\s]+"
)


def _redact_support_value(value):
    if isinstance(value, dict):
        return {key: _redact_support_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_support_value(item) for item in value]
    if not isinstance(value, str):
        return value
    text = _SUPPORT_URI_CREDENTIALS.sub(r"\1***:***@", value)
    text = _SUPPORT_BEARER.sub(r"\1 ***", text)
    text = _SUPPORT_QUERY_SECRET.sub(r"\1***", text)
    return text


def collect_support_bundle() -> dict:
    """Build a local-only, secret-free support bundle for troubleshooting."""
    diagnostics = collect_system_diagnostics()
    schema_version = 0
    migrations: list[dict] = []
    task_statuses: list[dict] = []
    task_types: list[dict] = []
    recent_tasks: list[dict] = []

    connection = sqlite3.connect(settings.db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        if _safe_table_count(connection, "schema_version"):
            row = connection.execute(
                "SELECT version, updated_at FROM schema_version WHERE singleton_id = 1"
            ).fetchone()
            if row:
                schema_version = int(row["version"] or 0)
                schema_updated_at = row["updated_at"]
            else:
                schema_updated_at = None
        else:
            schema_updated_at = None

        if _safe_table_count(connection, "schema_migrations"):
            migrations = [dict(row) for row in connection.execute(
                "SELECT version, name, checksum, applied_at, execution_ms "
                "FROM schema_migrations ORDER BY version"
            ).fetchall()]

        if _safe_table_count(connection, "manual_tasks"):
            task_statuses = [dict(row) for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM manual_tasks "
                "GROUP BY status ORDER BY status"
            ).fetchall()]
            task_types = [dict(row) for row in connection.execute(
                "SELECT task_type, COUNT(*) AS count FROM manual_tasks "
                "GROUP BY task_type ORDER BY task_type"
            ).fetchall()]
            recent_tasks = [dict(row) for row in connection.execute(
                """
                SELECT id, task_type, title, status, requested_by, total,
                       completed, succeeded, failed, skipped, interrupted,
                       error, created_at, started_at, finished_at, updated_at
                FROM manual_tasks ORDER BY id DESC LIMIT 20
                """
            ).fetchall()]
    finally:
        connection.close()

    backups = [
        {
            "name": item.get("name"),
            "size": int(item.get("size") or 0),
            "created_at": item.get("created_at"),
        }
        for item in list_backups()
    ]
    bundle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "service": settings.app_name,
        "version": settings.version,
        "display_version": settings.display_version,
        "privacy": "本文件不包含 OCI 私钥、代理密码、Telegram Token、Google Secret 或 .env 内容。",
        "diagnostics": diagnostics,
        "database": {
            "schema_version": schema_version,
            "schema_updated_at": schema_updated_at,
            "migrations": migrations,
        },
        "tasks": {
            "by_status": task_statuses,
            "by_type": task_types,
            "recent": recent_tasks,
        },
        "release": current_release_info(),
        "backups": {
            "policy": load_backup_policy(),
            "schedule": schedule_status(),
            "count": len(backups),
            "total_bytes": sum(item["size"] for item in backups),
            "files": backups,
        },
        "monitor": {
            "settings": load_monitor_settings(),
            "traffic": traffic_totals(),
            "containers": collect_container_status(),
        },
    }
    return _redact_support_value(bundle)

