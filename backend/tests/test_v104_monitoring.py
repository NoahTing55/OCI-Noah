from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.database import init_database
from app import monitor_agent
from app import system_monitor_service as monitor


def _use_temp_database(tmp_path) -> None:
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "oci-nt.db"
    settings.backup_dir = tmp_path / "backups"
    monitor._realtime_state.clear()
    monitor._persist_state.clear()
    init_database()


def test_schema_six_has_monitoring_tables(tmp_path):
    _use_temp_database(tmp_path)
    with sqlite3.connect(settings.db_path) as connection:
        version = connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id=1"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert version == 7
    assert {"system_metrics", "system_alert_states", "system_alert_events"} <= tables


def test_metric_sampling_persists_and_history_exports(tmp_path):
    _use_temp_database(tmp_path)
    first = monitor.collect_metric(persist=True)
    second = monitor.collect_metric(persist=True)
    assert first["memory_total_bytes"] >= first["memory_used_bytes"] >= 0
    assert second["interface"] == first["interface"]
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM system_metrics").fetchone()[0] == 2
    result = monitor.history("1h")
    assert result["range"] == "1h"
    assert result["points"]
    csv_text = monitor.export_history_csv("1h")
    assert "cpu_percent" in csv_text
    assert first["interface"] in csv_text


def test_traffic_totals_respect_reset_baseline(tmp_path):
    _use_temp_database(tmp_path)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(settings.db_path) as connection:
        for minutes, rx, tx in ((-20, 100, 50), (-10, 200, 75)):
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
                ((now + timedelta(minutes=minutes)).isoformat(), "boot", "eth0", 1, 0, 0, 0, 1, 1, 0, 0, 1, 1, rx, tx, rx, tx, 0, 0, 1),
            )
        connection.commit()
    monitor.set_setting("monitor_traffic_reset_at", (now - timedelta(minutes=15)).isoformat())
    totals = monitor.traffic_totals()
    assert totals["since_reset"] == {"rx_bytes": 200, "tx_bytes": 75}
    assert totals["month"]["rx_bytes"] >= 300


def test_monitor_settings_validate_interface(tmp_path, monkeypatch):
    _use_temp_database(tmp_path)
    monkeypatch.setattr(
        monitor,
        "list_network_interfaces",
        lambda: [{"name": "eth0", "state": "up", "virtual": False}],
    )
    saved = monitor.save_monitor_settings(
        {
            "enabled": True,
            "interface": "eth0",
            "sample_interval_seconds": 30,
            "retention_days": 7,
            "timezone_offset_minutes": 480,
            "include_all_containers": False,
            "cpu_alert_percent": 85,
            "swap_alert_percent": 65,
            "monthly_traffic_quota_gb": 100,
            "alert_duration_minutes": 3,
        }
    )
    assert saved["interface"] == "eth0"
    assert saved["retention_days"] == 7
    with pytest.raises(ValueError):
        monitor.save_monitor_settings({"interface": "missing0"})


def test_alert_transition_records_single_trigger_and_recovery(tmp_path, monkeypatch):
    _use_temp_database(tmp_path)
    messages = []
    monkeypatch.setattr(monitor, "send_configured_telegram", lambda text, category=None: messages.append((text, category)) or (True, "ok"))
    monitor._record_alert("cpu", True, "warning", {"summary": "high"}, 1)
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute(
            "UPDATE system_alert_states SET first_seen_at=? WHERE alert_key='cpu'",
            ((datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),),
        )
        connection.commit()
    monitor._record_alert("cpu", True, "warning", {"summary": "high"}, 1)
    monitor._record_alert("cpu", True, "warning", {"summary": "still high"}, 1)
    monitor._record_alert("cpu", False, "warning", {"summary": "normal"}, 1)
    alerts = monitor.list_alerts()
    assert [row["event_type"] for row in reversed(alerts["events"])] == ["TRIGGERED", "RECOVERED"]
    assert len(messages) == 2
    assert all(category == "system_resource" for _, category in messages)


def test_container_agent_filters_to_oci_nt_by_default(monkeypatch):
    rows = [
        {"Id": "a" * 64, "Names": ["/oci-nt-api"], "Image": "api", "State": "running"},
        {"Id": "b" * 64, "Names": ["/other-service"], "Image": "other", "State": "running"},
    ]

    def fake_get(path: str):
        if path == "/containers/json?all=1":
            return rows
        if path == "/version":
            return {"Version": "test"}
        if path.endswith("/json"):
            return {"State": {"Status": "running", "StartedAt": "now", "Health": {"Status": "healthy"}}, "RestartCount": 0}
        if "stats?stream=false" in path:
            return {"cpu_stats": {}, "precpu_stats": {}, "memory_stats": {}}
        raise AssertionError(path)

    monkeypatch.setattr(monitor_agent, "docker_get", fake_get)
    result = monitor_agent.collect_containers(include_all=False)
    assert [item["name"] for item in result["containers"]] == ["oci-nt-api"]
    assert result["docker_version"] == "test"


def test_current_snapshot_contains_requested_sections(tmp_path, monkeypatch):
    _use_temp_database(tmp_path)
    monkeypatch.setattr(monitor, "collect_container_status", lambda: {"available": False, "containers": [], "docker_version": None})
    payload = monitor.current_monitor_snapshot()
    assert {"metric", "system", "traffic", "containers", "settings", "interfaces", "alerts"} <= payload.keys()
    assert payload["system"]["cpu_cores"] >= 1
