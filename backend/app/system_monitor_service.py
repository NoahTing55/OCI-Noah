from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import platform
import socket
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import settings
from .database import database
from .settings_repository import get_setting, set_setting
from .system_resource_service import load_resource_limits
from .telegram_service import send_configured_telegram

MONITOR_AGENT_URL = "http://127.0.0.1:9860"
HOST_PROC = Path(os.getenv("HOST_PROC_PATH", "/host/proc"))
HOST_SYS = Path(os.getenv("HOST_SYS_PATH", "/host/sys"))
HOST_ETC = Path(os.getenv("HOST_ETC_PATH", "/host/etc"))


def _host_file(root: Path, fallback_root: Path, relative: str) -> Path:
    candidate = root / relative
    return candidate if candidate.exists() else fallback_root / relative


def _load_averages() -> tuple[float, float, float]:
    try:
        values = _host_file(HOST_PROC, Path("/proc"), "loadavg").read_text().split()[:3]
        return tuple(float(value) for value in values)  # type: ignore[return-value]
    except Exception:
        try:
            return os.getloadavg()
        except OSError:
            return 0.0, 0.0, 0.0

VIRTUAL_PREFIXES = ("lo", "docker", "veth", "br-", "virbr", "cni", "flannel", "kube", "tailscale")
DEFAULTS = {
    "enabled": True,
    "interface": "auto",
    "sample_interval_seconds": 60,
    "retention_days": 30,
    "timezone_offset_minutes": 480,
    "include_all_containers": False,
    "cpu_alert_percent": 90,
    "swap_alert_percent": 70,
    "monthly_traffic_quota_gb": 0,
    "alert_duration_minutes": 5,
    "traffic_reset_at": None,
}

_tracker_lock = threading.RLock()
_realtime_state: dict = {}
_persist_state: dict = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_monitor_settings() -> dict:
    def read_int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(get_setting(f"monitor_{name}") or default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    return {
        "enabled": _parse_bool(get_setting("monitor_enabled"), True),
        "interface": (get_setting("monitor_interface") or "auto").strip() or "auto",
        "sample_interval_seconds": read_int("sample_interval_seconds", 60, 30, 300),
        "retention_days": read_int("retention_days", 30, 1, 365),
        "timezone_offset_minutes": read_int("timezone_offset_minutes", 480, -720, 840),
        "include_all_containers": _parse_bool(get_setting("monitor_include_all_containers"), False),
        "cpu_alert_percent": read_int("cpu_alert_percent", 90, 10, 100),
        "swap_alert_percent": read_int("swap_alert_percent", 70, 10, 100),
        "monthly_traffic_quota_gb": read_int("monthly_traffic_quota_gb", 0, 0, 1000000),
        "alert_duration_minutes": read_int("alert_duration_minutes", 5, 1, 1440),
        "traffic_reset_at": get_setting("monitor_traffic_reset_at"),
    }


def save_monitor_settings(payload: dict) -> dict:
    current = load_monitor_settings()
    values = {
        "enabled": bool(payload.get("enabled", current["enabled"])),
        "interface": str(payload.get("interface", current["interface"]) or "auto").strip(),
        "sample_interval_seconds": max(30, min(int(payload.get("sample_interval_seconds", current["sample_interval_seconds"])), 300)),
        "retention_days": max(1, min(int(payload.get("retention_days", current["retention_days"])), 365)),
        "timezone_offset_minutes": max(-720, min(int(payload.get("timezone_offset_minutes", current["timezone_offset_minutes"])), 840)),
        "include_all_containers": bool(payload.get("include_all_containers", current["include_all_containers"])),
        "cpu_alert_percent": max(10, min(int(payload.get("cpu_alert_percent", current["cpu_alert_percent"])), 100)),
        "swap_alert_percent": max(10, min(int(payload.get("swap_alert_percent", current["swap_alert_percent"])), 100)),
        "monthly_traffic_quota_gb": max(0, min(int(payload.get("monthly_traffic_quota_gb", current["monthly_traffic_quota_gb"])), 1000000)),
        "alert_duration_minutes": max(1, min(int(payload.get("alert_duration_minutes", current["alert_duration_minutes"])), 1440)),
    }
    available = {item["name"] for item in list_network_interfaces()}
    if values["interface"] != "auto" and values["interface"] not in available:
        raise ValueError("选择的网卡不存在")
    for key, value in values.items():
        set_setting(f"monitor_{key}", "1" if isinstance(value, bool) and value else ("0" if isinstance(value, bool) else str(value)))
    return load_monitor_settings()


def reset_traffic_baseline() -> dict:
    stamp = utc_now()
    set_setting("monitor_traffic_reset_at", stamp)
    return {"reset_at": stamp}


def _read_os_release() -> dict:
    result = {}
    try:
        for line in _host_file(HOST_ETC, Path("/etc"), "os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"')
    except Exception:
        pass
    return result


def _cpu_model() -> str:
    try:
        for line in _host_file(HOST_PROC, Path("/proc"), "cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith(("model name", "hardware", "processor")) and ":" in line:
                value = line.split(":", 1)[1].strip()
                if value and not value.isdigit():
                    return value
    except Exception:
        pass
    return platform.processor() or "未知"


def _boot_id() -> str:
    try:
        return _host_file(HOST_PROC, Path("/proc"), "sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except Exception:
        return "unknown"


def _uptime_seconds() -> int:
    try:
        return max(0, int(float(_host_file(HOST_PROC, Path("/proc"), "uptime").read_text().split()[0])))
    except Exception:
        return 0


def collect_system_info() -> dict:
    os_release = _read_os_release()
    uptime = _uptime_seconds()
    boot_time = datetime.now(timezone.utc) - timedelta(seconds=uptime)
    load1, load5, load15 = _load_averages()
    try:
        hostname = _host_file(HOST_ETC, Path("/etc"), "hostname").read_text(encoding="utf-8").strip()
    except Exception:
        hostname = socket.gethostname()
    try:
        cpu_cores = sum(1 for line in _host_file(HOST_PROC, Path("/proc"), "cpuinfo").read_text(errors="replace").splitlines() if line.startswith("processor"))
    except Exception:
        cpu_cores = os.cpu_count() or 1
    return {
        "hostname": hostname,
        "operating_system": os_release.get("PRETTY_NAME") or platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_cores": cpu_cores or (os.cpu_count() or 1),
        "timezone": time.tzname[0] if time.tzname else "UTC",
        "current_time": utc_now(),
        "boot_time": boot_time.isoformat(),
        "uptime_seconds": uptime,
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
    }


def _read_cpu_ticks() -> tuple[int, int]:
    fields = _host_file(HOST_PROC, Path("/proc"), "stat").read_text().splitlines()[0].split()[1:]
    values = [int(value) for value in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _read_meminfo() -> dict[str, int]:
    result = {}
    try:
        for line in _host_file(HOST_PROC, Path("/proc"), "meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            result[key] = int(raw.strip().split()[0]) * 1024
    except Exception:
        pass
    return result


def _default_route_interface() -> str | None:
    try:
        for line in _host_file(HOST_PROC, Path("/proc"), "net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 4 and fields[1] == "00000000" and int(fields[3], 16) & 2:
                return fields[0]
    except Exception:
        pass
    return None


def list_network_interfaces() -> list[dict]:
    rows = []
    for path in sorted((HOST_SYS / "class/net" if (HOST_SYS / "class/net").exists() else Path("/sys/class/net")).glob("*")):
        name = path.name
        if name == "lo":
            continue
        try:
            state = (path / "operstate").read_text().strip()
        except Exception:
            state = "unknown"
        virtual = name.startswith(VIRTUAL_PREFIXES) or "/virtual/" in str(path.resolve())
        rows.append({"name": name, "state": state, "virtual": virtual})
    return rows


def _select_interface(configured: str) -> str:
    available = list_network_interfaces()
    names = {item["name"] for item in available}
    if configured != "auto" and configured in names:
        return configured
    default = _default_route_interface()
    if default in names:
        return str(default)
    physical_up = [item["name"] for item in available if not item["virtual"] and item["state"] == "up"]
    if physical_up:
        return physical_up[0]
    physical = [item["name"] for item in available if not item["virtual"]]
    return physical[0] if physical else (available[0]["name"] if available else "")


def _network_counters(interface: str) -> tuple[int, int]:
    if not interface:
        return 0, 0
    try:
        for line in _host_file(HOST_PROC, Path("/proc"), "net/dev").read_text().splitlines()[2:]:
            name, raw = line.split(":", 1)
            if name.strip() != interface:
                continue
            fields = raw.split()
            return int(fields[0]), int(fields[8])
    except Exception:
        pass
    return 0, 0


def _disk_usage() -> tuple[int, int, int]:
    stats = os.statvfs(settings.data_dir)
    total = int(stats.f_blocks * stats.f_frsize)
    free = int(stats.f_bavail * stats.f_frsize)
    return total, max(0, total - free), free


def _last_persisted(interface: str, boot_id: str) -> dict | None:
    with database() as connection:
        row = connection.execute(
            """
            SELECT collected_at, rx_total_bytes, tx_total_bytes
            FROM system_metrics
            WHERE interface = ? AND boot_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (interface, boot_id),
        ).fetchone()
    return dict(row) if row else None


def collect_metric(*, persist: bool = False) -> dict:
    monitor = load_monitor_settings()
    interface = _select_interface(monitor["interface"])
    now_monotonic = time.monotonic()
    now_iso = utc_now()
    boot_id = _boot_id()
    total_ticks, idle_ticks = _read_cpu_ticks()
    rx_total, tx_total = _network_counters(interface)
    mem = _read_meminfo()
    disk_total, disk_used, disk_free = _disk_usage()
    load1, load5, load15 = _load_averages()

    state_holder = _persist_state if persist else _realtime_state
    state_key = f"{boot_id}:{interface}"
    with _tracker_lock:
        previous = state_holder.get(state_key)
        if persist and previous is None:
            persisted = _last_persisted(interface, boot_id)
            if persisted:
                try:
                    previous_time = datetime.fromisoformat(str(persisted["collected_at"]).replace("Z", "+00:00")).timestamp()
                except Exception:
                    previous_time = time.time()
                previous = {
                    "monotonic": now_monotonic - max(1.0, time.time() - previous_time),
                    "rx": int(persisted["rx_total_bytes"] or 0),
                    "tx": int(persisted["tx_total_bytes"] or 0),
                    "cpu_total": total_ticks,
                    "cpu_idle": idle_ticks,
                }
        elapsed = max(0.001, now_monotonic - float((previous or {}).get("monotonic", now_monotonic)))
        cpu_delta = total_ticks - int((previous or {}).get("cpu_total", total_ticks))
        idle_delta = idle_ticks - int((previous or {}).get("cpu_idle", idle_ticks))
        cpu_percent = 0.0 if cpu_delta <= 0 else max(0.0, min(100.0, (cpu_delta - idle_delta) / cpu_delta * 100.0))
        previous_rx = int((previous or {}).get("rx", rx_total))
        previous_tx = int((previous or {}).get("tx", tx_total))
        rx_delta = max(0, rx_total - previous_rx)
        tx_delta = max(0, tx_total - previous_tx)
        state_holder.clear()
        state_holder[state_key] = {
            "monotonic": now_monotonic,
            "rx": rx_total,
            "tx": tx_total,
            "cpu_total": total_ticks,
            "cpu_idle": idle_ticks,
        }

    memory_total = int(mem.get("MemTotal", 0))
    memory_available = int(mem.get("MemAvailable", 0))
    swap_total = int(mem.get("SwapTotal", 0))
    swap_free = int(mem.get("SwapFree", 0))
    metric = {
        "collected_at": now_iso,
        "boot_id": boot_id,
        "interface": interface,
        "cpu_percent": round(cpu_percent, 2),
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
        "memory_total_bytes": memory_total,
        "memory_used_bytes": max(0, memory_total - memory_available),
        "memory_available_bytes": memory_available,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": max(0, swap_total - swap_free),
        "disk_total_bytes": disk_total,
        "disk_used_bytes": disk_used,
        "disk_free_bytes": disk_free,
        "rx_total_bytes": rx_total,
        "tx_total_bytes": tx_total,
        "rx_delta_bytes": rx_delta,
        "tx_delta_bytes": tx_delta,
        "rx_rate_bps": round(rx_delta / elapsed, 2),
        "tx_rate_bps": round(tx_delta / elapsed, 2),
        "uptime_seconds": _uptime_seconds(),
    }
    if persist:
        with database() as connection:
            connection.execute(
                """
                INSERT INTO system_metrics(
                    collected_at, boot_id, interface, cpu_percent,
                    load1, load5, load15, memory_total_bytes, memory_used_bytes,
                    swap_total_bytes, swap_used_bytes, disk_total_bytes, disk_used_bytes,
                    rx_total_bytes, tx_total_bytes, rx_delta_bytes, tx_delta_bytes,
                    rx_rate_bps, tx_rate_bps, uptime_seconds
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    metric["collected_at"], metric["boot_id"], metric["interface"], metric["cpu_percent"],
                    metric["load1"], metric["load5"], metric["load15"], metric["memory_total_bytes"], metric["memory_used_bytes"],
                    metric["swap_total_bytes"], metric["swap_used_bytes"], metric["disk_total_bytes"], metric["disk_used_bytes"],
                    metric["rx_total_bytes"], metric["tx_total_bytes"], metric["rx_delta_bytes"], metric["tx_delta_bytes"],
                    metric["rx_rate_bps"], metric["tx_rate_bps"], metric["uptime_seconds"],
                ),
            )
    return metric


def _local_boundary(now: datetime, offset_minutes: int, *, month_delta: int = 0) -> datetime:
    offset = timezone(timedelta(minutes=offset_minutes))
    local = now.astimezone(offset)
    year, month = local.year, local.month + month_delta
    while month <= 0:
        year -= 1
        month += 12
    while month > 12:
        year += 1
        month -= 12
    boundary_local = datetime(year, month, 1, tzinfo=offset)
    return boundary_local.astimezone(timezone.utc)


def traffic_totals() -> dict:
    monitor = load_monitor_settings()
    now = datetime.now(timezone.utc)
    offset = timezone(timedelta(minutes=monitor["timezone_offset_minutes"]))
    local = now.astimezone(offset)
    today_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    month_start = _local_boundary(now, monitor["timezone_offset_minutes"])
    previous_month_start = _local_boundary(now, monitor["timezone_offset_minutes"], month_delta=-1)
    reset_at = monitor.get("traffic_reset_at") or month_start.isoformat()

    def sum_between(start: str, end: str | None = None) -> dict:
        sql = "SELECT COALESCE(SUM(rx_delta_bytes),0), COALESCE(SUM(tx_delta_bytes),0) FROM system_metrics WHERE collected_at >= ?"
        params: list = [start]
        if end:
            sql += " AND collected_at < ?"
            params.append(end)
        with database() as connection:
            row = connection.execute(sql, params).fetchone()
        return {"rx_bytes": int(row[0] or 0), "tx_bytes": int(row[1] or 0)}

    return {
        "today": sum_between(today_start.isoformat()),
        "month": sum_between(month_start.isoformat()),
        "previous_month": sum_between(previous_month_start.isoformat(), month_start.isoformat()),
        "since_reset": sum_between(reset_at),
        "reset_at": reset_at,
    }


def collect_container_status() -> dict:
    monitor = load_monitor_settings()
    url = f"{MONITOR_AGENT_URL}/containers?all={'1' if monitor['include_all_containers'] else '0'}"
    try:
        with urllib.request.urlopen(url, timeout=6) as response:
            payload = json.load(response)
        return payload
    except Exception as exc:
        containers = [
            {"name": "oci-nt-api", "state": "running", "health": "healthy", "cpu_percent": None, "memory_used_bytes": None, "restart_count": None},
        ]
        try:
            with urllib.request.urlopen("http://127.0.0.1:9859/", timeout=3):
                containers.append({"name": "oci-nt-web", "state": "running", "health": "healthy", "cpu_percent": None, "memory_used_bytes": None, "restart_count": None})
        except Exception:
            containers.append({"name": "oci-nt-web", "state": "unknown", "health": "unhealthy", "cpu_percent": None, "memory_used_bytes": None, "restart_count": None})
        return {"available": False, "error": f"{type(exc).__name__}: {exc}", "containers": containers, "docker_version": None}


def current_monitor_snapshot() -> dict:
    metric = collect_metric(persist=False)
    return {
        "metric": metric,
        "system": collect_system_info(),
        "traffic": traffic_totals(),
        "containers": collect_container_status(),
        "settings": load_monitor_settings(),
        "interfaces": list_network_interfaces(),
        "alerts": list_alerts(limit=20),
    }


def _range_definition(range_name: str) -> tuple[timedelta, int]:
    value = str(range_name or "1h").lower()
    return {
        "1h": (timedelta(hours=1), 60),
        "24h": (timedelta(hours=24), 300),
        "7d": (timedelta(days=7), 3600),
        "30d": (timedelta(days=30), 21600),
    }.get(value, (timedelta(hours=1), 60))


def history(range_name: str = "1h") -> dict:
    window, bucket_seconds = _range_definition(range_name)
    start = datetime.now(timezone.utc) - window
    with database() as connection:
        rows = connection.execute(
            """
            SELECT collected_at, cpu_percent, load1, memory_used_bytes, memory_total_bytes,
                   swap_used_bytes, swap_total_bytes, rx_rate_bps, tx_rate_bps
            FROM system_metrics WHERE collected_at >= ? ORDER BY id
            """,
            (start.isoformat(),),
        ).fetchall()
    buckets: dict[int, list] = {}
    for row in rows:
        try:
            stamp = int(datetime.fromisoformat(str(row["collected_at"]).replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
        buckets.setdefault(stamp // bucket_seconds, []).append(row)
    points = []
    for key in sorted(buckets):
        group = buckets[key]
        average = lambda field: sum(float(item[field] or 0) for item in group) / max(1, len(group))
        memory_total = average("memory_total_bytes")
        swap_total = average("swap_total_bytes")
        points.append({
            "time": datetime.fromtimestamp(key * bucket_seconds, timezone.utc).isoformat(),
            "cpu_percent": round(average("cpu_percent"), 2),
            "load1": round(average("load1"), 2),
            "memory_percent": round(average("memory_used_bytes") / memory_total * 100, 2) if memory_total else 0,
            "swap_percent": round(average("swap_used_bytes") / swap_total * 100, 2) if swap_total else 0,
            "rx_rate_bps": round(average("rx_rate_bps"), 2),
            "tx_rate_bps": round(average("tx_rate_bps"), 2),
        })
    return {"range": range_name, "bucket_seconds": bucket_seconds, "points": points}


def export_history_csv(range_name: str = "30d") -> str:
    window, _ = _range_definition(range_name)
    start = datetime.now(timezone.utc) - window
    with database() as connection:
        rows = connection.execute(
            """
            SELECT collected_at, interface, cpu_percent, load1, load5, load15,
                   memory_used_bytes, memory_total_bytes, swap_used_bytes, swap_total_bytes,
                   disk_used_bytes, disk_total_bytes, rx_delta_bytes, tx_delta_bytes,
                   rx_rate_bps, tx_rate_bps, uptime_seconds
            FROM system_metrics WHERE collected_at >= ? ORDER BY id
            """,
            (start.isoformat(),),
        ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    headers = ["collected_at", "interface", "cpu_percent", "load1", "load5", "load15", "memory_used_bytes", "memory_total_bytes", "swap_used_bytes", "swap_total_bytes", "disk_used_bytes", "disk_total_bytes", "rx_delta_bytes", "tx_delta_bytes", "rx_rate_bps", "tx_rate_bps", "uptime_seconds"]
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row[header] for header in headers])
    return output.getvalue()


def _alert_message(key: str, recovered: bool, detail: dict) -> str:
    labels = {
        "cpu": "CPU 使用率过高",
        "memory": "可用内存不足",
        "disk": "磁盘空间不足",
        "swap": "Swap 使用率过高",
        "traffic": "本月流量达到额度",
        "web": "Web 服务异常",
    }
    title = labels.get(key, key)
    state = "恢复" if recovered else "告警"
    return "\n".join([
        f"OCI-N&T 系统资源{state}", "", f"系统版本：{settings.display_version}", f"项目：{title}", f"详情：{detail.get('summary') or detail}", f"时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
    ])


def _record_alert(key: str, condition: bool, severity: str, detail: dict, duration_minutes: int) -> None:
    now = datetime.now(timezone.utc)
    with database() as connection:
        row = connection.execute("SELECT * FROM system_alert_states WHERE alert_key = ?", (key,)).fetchone()
        current = dict(row) if row else None
        old_status = str((current or {}).get("status") or "NORMAL")
        first_seen = (current or {}).get("first_seen_at")
        new_status = old_status
        notify = None
        if condition:
            if old_status == "NORMAL":
                new_status = "PENDING"
                first_seen = now.isoformat()
            elif old_status == "PENDING":
                try:
                    seen = datetime.fromisoformat(str(first_seen).replace("Z", "+00:00"))
                except Exception:
                    seen = now
                if now - seen >= timedelta(minutes=duration_minutes):
                    new_status = "ALERT"
                    notify = False
            elif old_status == "ALERT":
                new_status = "ALERT"
        else:
            if old_status == "ALERT":
                notify = True
            new_status = "NORMAL"
            first_seen = None
        changed = new_status != old_status
        connection.execute(
            """
            INSERT INTO system_alert_states(alert_key, status, severity, first_seen_at, last_changed_at, last_checked_at, detail_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(alert_key) DO UPDATE SET status=excluded.status, severity=excluded.severity,
                first_seen_at=excluded.first_seen_at,
                last_changed_at=CASE WHEN system_alert_states.status <> excluded.status THEN excluded.last_changed_at ELSE system_alert_states.last_changed_at END,
                last_checked_at=excluded.last_checked_at, detail_json=excluded.detail_json
            """,
            (key, new_status, severity, first_seen, now.isoformat(), now.isoformat(), json.dumps(detail, ensure_ascii=False)),
        )
        if changed and new_status in {"ALERT", "NORMAL"} and (old_status == "ALERT" or new_status == "ALERT"):
            event_type = "RECOVERED" if new_status == "NORMAL" else "TRIGGERED"
            connection.execute(
                "INSERT INTO system_alert_events(alert_key, severity, event_type, detail_json, created_at) VALUES (?,?,?,?,?)",
                (key, severity, event_type, json.dumps(detail, ensure_ascii=False), now.isoformat()),
            )
    if notify is not None:
        send_configured_telegram(_alert_message(key, notify, detail), category="system_resource")


def evaluate_alerts(metric: dict) -> None:
    monitor = load_monitor_settings()
    limits = load_resource_limits()
    duration = monitor["alert_duration_minutes"]
    memory_available = int(metric.get("memory_available_bytes") or 0)
    memory_limit = limits["min_free_memory_mb"] * 1024 * 1024
    disk_free = int(metric.get("disk_free_bytes") or 0)
    disk_limit = limits["min_free_disk_mb"] * 1024 * 1024
    swap_total = int(metric.get("swap_total_bytes") or 0)
    swap_used = int(metric.get("swap_used_bytes") or 0)
    swap_percent = swap_used / swap_total * 100 if swap_total else 0
    traffic = traffic_totals()["month"]
    traffic_total = int(traffic["rx_bytes"]) + int(traffic["tx_bytes"])
    quota = monitor["monthly_traffic_quota_gb"] * 1024**3
    containers = collect_container_status().get("containers") or []
    web = next((item for item in containers if item.get("name") == "oci-nt-web"), {})
    _record_alert("cpu", metric["cpu_percent"] >= monitor["cpu_alert_percent"], "warning", {"summary": f"CPU {metric['cpu_percent']:.1f}% / 阈值 {monitor['cpu_alert_percent']}%"}, duration)
    _record_alert("memory", memory_available < memory_limit, "error", {"summary": f"可用内存 {memory_available} bytes / 阈值 {memory_limit} bytes"}, duration)
    _record_alert("disk", disk_free < disk_limit, "error", {"summary": f"磁盘剩余 {disk_free} bytes / 阈值 {disk_limit} bytes"}, duration)
    _record_alert("swap", bool(swap_total) and swap_percent >= monitor["swap_alert_percent"], "warning", {"summary": f"Swap {swap_percent:.1f}% / 阈值 {monitor['swap_alert_percent']}%"}, duration)
    _record_alert("traffic", bool(quota) and traffic_total >= quota, "warning", {"summary": f"本月流量 {traffic_total} bytes / 额度 {quota} bytes"}, 1)
    _record_alert("web", str(web.get("health")) not in {"healthy", "none"} or str(web.get("state")) != "running", "error", {"summary": f"Web state={web.get('state') or 'unknown'} health={web.get('health') or 'unknown'}"}, duration)


def list_alerts(limit: int = 100) -> dict:
    with database() as connection:
        states = [dict(row) for row in connection.execute("SELECT * FROM system_alert_states ORDER BY alert_key").fetchall()]
        events = [dict(row) for row in connection.execute("SELECT * FROM system_alert_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()]
    for row in states + events:
        try:
            row["detail"] = json.loads(row.pop("detail_json") or "{}")
        except Exception:
            row["detail"] = {}
    return {"states": states, "events": events}


def cleanup_history(retention_days: int | None = None) -> dict:
    days = retention_days or load_monitor_settings()["retention_days"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    with database() as connection:
        cursor = connection.execute("DELETE FROM system_metrics WHERE collected_at < ?", (cutoff.isoformat(),))
        event_cursor = connection.execute("DELETE FROM system_alert_events WHERE created_at < ?", ((cutoff - timedelta(days=60)).isoformat(),))
    return {"metrics_deleted": cursor.rowcount, "alerts_deleted": event_cursor.rowcount, "cutoff": cutoff.isoformat()}


async def monitor_scheduler_loop(stop_event: asyncio.Event) -> None:
    await asyncio.sleep(2)
    cleanup_counter = 0
    while not stop_event.is_set():
        monitor = load_monitor_settings()
        interval = monitor["sample_interval_seconds"]
        try:
            if monitor["enabled"]:
                metric = await asyncio.to_thread(collect_metric, persist=True)
                await asyncio.to_thread(evaluate_alerts, metric)
                cleanup_counter += 1
                if cleanup_counter >= max(1, int(3600 / interval)):
                    await asyncio.to_thread(cleanup_history)
                    cleanup_counter = 0
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
