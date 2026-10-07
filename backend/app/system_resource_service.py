from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .database import database
from .settings_repository import get_setting, set_setting

DEFAULTS = {
    "min_free_memory_mb": 384,
    "min_free_disk_mb": 1024,
    "max_active_tasks": 2,
    "max_task_items": 100,
}


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except Exception:
        pass
    return values


def _active_tasks() -> int:
    with database() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM manual_tasks WHERE status IN ('PENDING','RUNNING','WAITING','CANCELLING')"
        ).fetchone()
    return int(row[0] if row else 0)


def load_resource_limits() -> dict:
    result = {}
    for key, default in DEFAULTS.items():
        raw = get_setting(f"resource_{key}") or str(default)
        try:
            result[key] = int(raw)
        except (TypeError, ValueError):
            result[key] = default
    result["min_free_memory_mb"] = max(128, min(result["min_free_memory_mb"], 65536))
    result["min_free_disk_mb"] = max(256, min(result["min_free_disk_mb"], 1048576))
    result["max_active_tasks"] = max(1, min(result["max_active_tasks"], 20))
    result["max_task_items"] = max(1, min(result["max_task_items"], 1000))
    return result


def save_resource_limits(payload: dict) -> dict:
    current = load_resource_limits()
    for key in DEFAULTS:
        if key not in payload:
            continue
        value = int(payload[key])
        if key == "min_free_memory_mb": value = max(128, min(value, 65536))
        elif key == "min_free_disk_mb": value = max(256, min(value, 1048576))
        elif key == "max_active_tasks": value = max(1, min(value, 20))
        elif key == "max_task_items": value = max(1, min(value, 1000))
        set_setting(f"resource_{key}", str(value))
        current[key] = value
    return current


def collect_resource_status() -> dict:
    mem = _meminfo()
    disk = os.statvfs(settings.data_dir)
    db_path = settings.db_path
    wal_path = Path(str(db_path) + "-wal")
    limits = load_resource_limits()
    available_memory = int(mem.get("MemAvailable", 0))
    swap_free = int(mem.get("SwapFree", 0))
    disk_free = int(disk.f_bavail * disk.f_frsize)
    active_tasks = _active_tasks()
    warnings = []
    if available_memory + swap_free < limits["min_free_memory_mb"] * 1024 * 1024:
        warnings.append("可用内存和 Swap 低于任务保护阈值")
    if disk_free < limits["min_free_disk_mb"] * 1024 * 1024:
        warnings.append("数据盘可用空间低于保护阈值")
    if active_tasks >= limits["max_active_tasks"]:
        warnings.append("运行中任务数量已达到限制")
    wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
    if wal_bytes > 512 * 1024 * 1024:
        warnings.append("SQLite WAL 已超过 512 MiB，建议检查数据库")
    return {
        "ok": not warnings,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "memory_total_bytes": int(mem.get("MemTotal", 0)),
        "memory_available_bytes": available_memory,
        "swap_total_bytes": int(mem.get("SwapTotal", 0)),
        "swap_free_bytes": swap_free,
        "disk_total_bytes": int(disk.f_blocks * disk.f_frsize),
        "disk_free_bytes": disk_free,
        "inode_free": int(disk.f_favail),
        "database_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "wal_bytes": wal_bytes,
        "active_tasks": active_tasks,
        "limits": limits,
        "warnings": warnings,
    }


def assert_task_resources(item_count: int) -> dict:
    status = collect_resource_status()
    limits = status["limits"]
    if int(item_count) > limits["max_task_items"]:
        raise RuntimeError(f"单个任务项目数超过系统限制 {limits['max_task_items']}")
    if status["active_tasks"] >= limits["max_active_tasks"]:
        raise RuntimeError(f"运行中任务已达到系统限制 {limits['max_active_tasks']}")
    if status["memory_available_bytes"] + status["swap_free_bytes"] < limits["min_free_memory_mb"] * 1024 * 1024:
        raise RuntimeError("服务器可用内存不足，已阻止启动新的批量任务")
    if status["disk_free_bytes"] < limits["min_free_disk_mb"] * 1024 * 1024:
        raise RuntimeError("服务器磁盘空间不足，已阻止启动新的批量任务")
    return status

def assert_backup_resources(required_bytes: int | None = None) -> dict:
    status = collect_resource_status()
    limits = status["limits"]
    minimum = limits["min_free_disk_mb"] * 1024 * 1024
    estimated = max(int(required_bytes or 0) * 2, 64 * 1024 * 1024)
    if status["disk_free_bytes"] < max(minimum, estimated):
        raise RuntimeError(
            "服务器磁盘空间不足，已阻止创建备份；请清理旧备份或释放磁盘空间"
        )
    return status

