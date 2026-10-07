from pathlib import Path

from app.config import settings


def test_v2_version_and_final_router_names():
    root = Path(__file__).resolve().parents[1]
    assert settings.version == "2.0.0"
    assert settings.display_version == "N&T 2.0"
    assert (root / "app/instance_router.py").exists()
    assert (root / "app/launch_router.py").exists()
    assert (root / "app/resource_router.py").exists()
    assert not (root / "app/v4_router.py").exists()
    assert not (root / "app/v7_launch_router.py").exists()
    assert not (root / "app/v7_rc_router.py").exists()


def test_instance_workspace_contains_final_tabs_and_filtered_audit():
    root = Path(__file__).resolve().parents[2]
    app_js = (root / "frontend/app.js").read_text(encoding="utf-8")
    main_py = (root / "backend/app/main.py").read_text(encoding="utf-8")
    database_py = (root / "backend/app/database.py").read_text(encoding="utf-8")
    for label in ["概览", "网络", "存储", "监控", "控制台", "操作记录"]:
        assert label in app_js
    assert "resource_id: str | None = None" in main_py
    assert "resource_id: str | None = None" in database_py
    assert "data-detail-history-list" in app_js
    assert "data-detail-console-sessions" in app_js


def test_frontend_does_not_ship_patch_backup_files():
    root = Path(__file__).resolve().parents[2]
    assert not list((root / "frontend").glob("*.bak-*"))
    assert not list((root / "backend/app").glob("*.bak-*"))
