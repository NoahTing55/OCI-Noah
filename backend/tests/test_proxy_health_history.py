from __future__ import annotations

from app.config import settings
from app.database import init_database
from app.task_repository import create_task
from app.proxy_repository import (
    create_proxy_profile,
    get_proxy_profile,
    list_proxy_health_history,
    save_proxy_profile_test_result,
)


def test_proxy_health_success_and_failure_are_persisted(tmp_path):
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "oci-nt.db"
    settings.backup_dir = tmp_path / "backups"
    init_database()
    profile = create_proxy_profile(
        name="测试代理", proxy_url="http://127.0.0.1:8080"
    )
    task = create_task(
        task_type="PROXY_HEALTH", title="代理检测", requested_by="tester",
        items=[{"item_key": str(profile["id"]), "item_name": profile["name"]}],
    )

    save_proxy_profile_test_result(
        profile["id"],
        ip_address="203.0.113.8",
        tested_at="2026-07-31T12:00:00+00:00",
        error=None,
        latency_ms=120,
        country_code="TW",
        country_name="Taiwan",
        region_name="Taipei",
        city="Taipei",
        task_id=task["id"],
    )
    healthy = get_proxy_profile(profile["id"])
    assert healthy["last_ip"] == "203.0.113.8"
    assert healthy["last_latency_ms"] == 120
    assert healthy["consecutive_failures"] == 0

    save_proxy_profile_test_result(
        profile["id"],
        ip_address=None,
        tested_at="2026-07-31T12:01:00+00:00",
        error="timeout",
        task_id=task["id"],
    )
    failed = get_proxy_profile(profile["id"])
    assert failed["last_ip"] == "203.0.113.8"
    assert failed["consecutive_failures"] == 1
    assert failed["last_error"] == "timeout"

    history = list_proxy_health_history(profile["id"])
    assert [item["success"] for item in history[:2]] == [False, True]
    assert history[0]["task_id"] == task["id"]
