from __future__ import annotations

import os
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
work = Path(tempfile.mkdtemp(prefix="oci-nt-proxy-library-"))
os.environ.update(
    SECRET_KEY="test-secret-key",
    CREDENTIAL_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    ADMIN_USERNAME="Noah",
    ADMIN_PASSWORD="x",
    DATA_DIR=str(work),
    DB_PATH=str(work / "oci-nt.db"),
    BACKUP_DIR=str(work / "backups"),
)

import sys
sys.path.insert(0, str(root / "backend"))

from app.account_repository import get_account_public  # noqa: E402
from app.credential_crypto import decrypt_secret, encrypt_secret  # noqa: E402
from app.database import database, init_database  # noqa: E402
from app.proxy_repository import (  # noqa: E402
    bind_account_proxy,
    create_proxy_profile,
    delete_proxy_profile,
    get_proxy_profile_with_secret,
    list_proxy_profiles,
    migrate_legacy_account_proxies,
)

init_database()
with database() as connection:
    connection.execute(
        """
        INSERT INTO oci_accounts (
            custom_name, tenancy_ocid, user_ocid, fingerprint, region,
            private_key_encrypted, proxy_enabled, proxy_url_encrypted,
            proxy_label, account_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "旧账户", "ocid1.tenancy.test", "ocid1.user.test", "aa:bb",
            "uk-london-1", encrypt_secret("private-key"), 1,
            encrypt_secret("socks5://old:secret@127.0.0.1:1080"),
            "旧伦敦代理", "ALIVE",
        ),
    )

assert migrate_legacy_account_proxies() == 1
profiles = list_proxy_profiles()
assert len(profiles) == 1 and profiles[0]["name"] == "旧伦敦代理"
account = get_account_public(1)
assert account and account["proxy_profile_id"] == profiles[0]["id"]
assert account["proxy_profile_name"] == "旧伦敦代理"
secret = get_proxy_profile_with_secret(profiles[0]["id"])
assert secret and decrypt_secret(secret["proxy_url_encrypted"]).endswith("127.0.0.1:1080")

new_profile = create_proxy_profile(
    name="新加坡出口", proxy_url="http://user:pass@127.0.0.2:8080"
)
bind_account_proxy(account_id=1, profile_id=new_profile["id"], enabled=True)
account = get_account_public(1)
assert account and account["proxy_profile_name"] == "新加坡出口"
assert account["proxy_enabled"] == 1

deleted, detached = delete_proxy_profile(new_profile["id"])
assert deleted and detached == 1
account = get_account_public(1)
assert account and account["proxy_profile_id"] is None and account["proxy_enabled"] == 0
assert not account["has_proxy"]
print("PROXY_LIBRARY_SMOKE_OK")
