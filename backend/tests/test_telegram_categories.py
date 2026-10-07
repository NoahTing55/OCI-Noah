from __future__ import annotations

from app.config import settings
from app.database import init_database
from app.settings_repository import (
    load_telegram_settings,
    save_telegram_settings,
    telegram_category_enabled,
)


def _use_temp_database(tmp_path) -> None:
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "oci-nt.db"
    settings.backup_dir = tmp_path / "backups"
    init_database()


def test_telegram_categories_default_enabled_and_persist(tmp_path):
    _use_temp_database(tmp_path)
    defaults = load_telegram_settings()
    assert defaults["account_check"] is True
    assert defaults["instance_operation"] is True
    assert defaults["launch_task"] is True
    assert defaults["proxy_alert"] is True
    assert defaults["system_backup"] is True

    saved = save_telegram_settings(
        enabled=True,
        chat_id="123",
        bot_token="token",
        account_check=False,
        instance_operation=True,
        launch_task=False,
        proxy_alert=True,
        system_backup=False,
    )
    assert saved["account_check"] is False
    assert saved["instance_operation"] is True
    assert saved["launch_task"] is False
    assert saved["proxy_alert"] is True
    assert saved["system_backup"] is False
    assert telegram_category_enabled("account_check") is False
    assert telegram_category_enabled("instance_operation") is True
    assert telegram_category_enabled("unknown-category") is True
