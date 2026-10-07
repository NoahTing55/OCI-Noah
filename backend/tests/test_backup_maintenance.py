from __future__ import annotations

from app.backup_service import (
    create_sqlite_backup, maintain_sqlite_database, verify_sqlite_backup,
)
from app.config import settings
from app.database import init_database


def _use_temp_database(tmp_path) -> None:
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "oci-nt.db"
    settings.backup_dir = tmp_path / "backups"
    init_database()


def test_backup_is_verified_and_database_maintenance_is_safe(tmp_path):
    _use_temp_database(tmp_path)
    backup = create_sqlite_backup("test")
    assert backup["ok"] is True
    assert backup["quick_check"] == "ok"
    assert len(backup["sha256"]) == 64

    verified = verify_sqlite_backup(backup["name"])
    assert verified["ok"] is True
    assert verified["sha256"] == backup["sha256"]

    maintenance = maintain_sqlite_database(vacuum=False)
    assert maintenance["ok"] is True
    assert maintenance["quick_check_before"] == "ok"
    assert maintenance["quick_check_after"] == "ok"
