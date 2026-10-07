import base64
import os
import tempfile
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory(prefix="oci-nt-proxy-library-test-")
_DATA_DIR = Path(_TEMP_DIR.name)
os.environ["SECRET_KEY"] = "proxy-library-test-secret"
os.environ["CREDENTIAL_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(b"p" * 32).decode()
os.environ["ADMIN_PASSWORD"] = "x"
os.environ["DATA_DIR"] = str(_DATA_DIR)
os.environ["DB_PATH"] = str(_DATA_DIR / "oci-nt.db")
os.environ["BACKUP_DIR"] = str(_DATA_DIR / "backups")

from app.account_repository import get_account_public
from app.credential_crypto import decrypt_secret, encrypt_secret
from app.database import database, init_database
from app.proxy_repository import (
    bind_account_proxy,
    create_proxy_profile,
    delete_proxy_profile,
    get_proxy_profile_with_secret,
    get_proxy_rotation_config,
    migrate_legacy_account_proxies,
)
from app.proxy_import import parse_proxy_import


_ACCOUNT_SEQUENCE = 0


def _insert_account(*, legacy_proxy: bool = False) -> int:
    global _ACCOUNT_SEQUENCE
    _ACCOUNT_SEQUENCE += 1
    suffix = str(_ACCOUNT_SEQUENCE)
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO oci_accounts (
                custom_name, tenancy_ocid, user_ocid, fingerprint, region,
                private_key_encrypted, proxy_enabled, proxy_url_encrypted,
                proxy_label, account_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"伦敦-{suffix}", f"ocid1.tenancy.proxytest{suffix}", f"ocid1.user.proxytest{suffix}",
                f"aa:bb:{suffix}", "uk-london-1", encrypt_secret("private-key"),
                1 if legacy_proxy else 0,
                encrypt_secret("socks5://user:pass@127.0.0.1:1080") if legacy_proxy else None,
                "旧代理" if legacy_proxy else None,
                "ALIVE",
            ),
        )
        return int(cursor.lastrowid)


def setup_module():
    init_database()


def test_proxy_profile_is_encrypted_and_bound_by_id():
    account_id = _insert_account()
    profile = create_proxy_profile(
        name="伦敦出口", proxy_url="socks5://user:pass@127.0.0.2:1080"
    )
    stored = get_proxy_profile_with_secret(profile["id"])
    assert stored["proxy_url_encrypted"] != "socks5://user:pass@127.0.0.2:1080"
    assert decrypt_secret(stored["proxy_url_encrypted"]).endswith("127.0.0.2:1080")

    bind_account_proxy(account_id=account_id, profile_id=profile["id"], enabled=True)
    account = get_account_public(account_id)
    assert account["proxy_profile_id"] == profile["id"]
    assert account["proxy_profile_name"] == "伦敦出口"
    assert account["proxy_enabled"] == 1


def test_legacy_account_proxy_migrates_into_library():
    account_id = _insert_account(legacy_proxy=True)
    assert migrate_legacy_account_proxies() == 1
    account = get_account_public(account_id)
    assert account["proxy_profile_id"] is not None
    assert account["proxy_profile_name"] == "旧代理"


def test_delete_profile_detaches_and_disables_account():
    account_id = _insert_account()
    profile = create_proxy_profile(name="待删除代理", proxy_url="http://127.0.0.3:8080")
    bind_account_proxy(account_id=account_id, profile_id=profile["id"], enabled=True)
    deleted, detached = delete_proxy_profile(profile["id"])
    assert deleted is True
    assert detached == 1
    account = get_account_public(account_id)
    assert account["proxy_profile_id"] is None
    assert account["proxy_enabled"] == 0
    assert account["has_proxy"] == 0


def test_proxy_import_accepts_common_formats():
    items = parse_proxy_import(
        """
        socks5://user:pass@127.0.0.10:1080
        127.0.0.11:8080
        127.0.0.12:9000:user2:pass2
        user3:pass3@127.0.0.13:10000
        伦敦|http://127.0.0.14:3128|https://provider.example/rotate?id=14
        """,
        default_scheme="http",
        name_prefix="导入",
    )
    assert len(items) == 5
    assert items[0].proxy_url.startswith("socks5://")
    assert items[1].proxy_url == "http://127.0.0.11:8080"
    assert items[2].proxy_url.startswith("http://user2:pass2@")
    assert items[3].proxy_url.startswith("http://user3:pass3@")
    assert items[4].name == "伦敦"
    assert items[4].rotate_api_url.startswith("https://provider.example/")


def test_proxy_import_accepts_json_with_rotation_api():
    content = '[{"name":"JSON代理","proxy_url":"socks5h://u:p@127.0.0.20:1080","rotate_api_url":"https://provider.example/change","rotate_api_method":"POST","rotate_api_headers":{"Authorization":"Bearer x"},"rotate_api_body":"{\\"rotate\\":true}","rotate_wait_seconds":5}]'
    items = parse_proxy_import(content)
    assert len(items) == 1
    assert items[0].rotate_api_method == "POST"
    assert items[0].rotate_api_headers["Authorization"] == "Bearer x"
    assert items[0].rotate_wait_seconds == 5


def test_rotation_api_secrets_are_encrypted():
    profile = create_proxy_profile(
        name="动态代理",
        proxy_url="socks5://user:pass@127.0.0.21:1080",
        rotation_config={
            "api_url": "https://provider.example/change?token=secret",
            "method": "POST",
            "headers": {"Authorization": "Bearer secret"},
            "body": '{"action":"rotate"}',
            "wait_seconds": 4,
        },
    )
    stored = get_proxy_profile_with_secret(profile["id"])
    assert stored["rotate_api_url_encrypted"] != "https://provider.example/change?token=secret"
    assert "Bearer secret" not in str(stored)
    config = get_proxy_rotation_config(profile["id"])
    assert config["method"] == "POST"
    assert config["headers"]["Authorization"] == "Bearer secret"
    assert config["wait_seconds"] == 4


def test_structured_proxy_edit_preserves_password_and_can_clear_auth():
    from app.proxy_repository import (
        build_proxy_url_update,
        public_profile_for_edit,
        update_proxy_profile,
    )

    profile = create_proxy_profile(
        name="结构化编辑代理",
        proxy_url="socks5://old-user:old-pass@127.0.0.31:1080",
    )
    detail = public_profile_for_edit(profile["id"])
    assert detail["username"] == "old-user"
    assert "old-pass" not in str(detail)

    updated_url = build_proxy_url_update(
        profile["id"],
        {
            "scheme": "http",
            "host": "127.0.0.32",
            "port": 8080,
            "username": "new-user",
            "password": "",
        },
    )
    assert updated_url == "http://new-user:old-pass@127.0.0.32:8080"
    update_proxy_profile(profile["id"], proxy_url=updated_url)
    stored = get_proxy_profile_with_secret(profile["id"])
    assert decrypt_secret(stored["proxy_url_encrypted"]) == updated_url

    cleared_url = build_proxy_url_update(
        profile["id"],
        {"host": "127.0.0.33", "clear_auth": True},
    )
    assert cleared_url == "http://127.0.0.33:8080"
