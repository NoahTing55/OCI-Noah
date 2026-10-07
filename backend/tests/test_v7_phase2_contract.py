import base64
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory(prefix="oci-nt-v7-phase2-")
_DATA_DIR = Path(_TEMP_DIR.name)
os.environ["SECRET_KEY"] = "v7-phase2-test-secret"
os.environ["CREDENTIAL_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(b"8" * 32).decode()
os.environ["ADMIN_PASSWORD"] = "x"
os.environ["DATA_DIR"] = str(_DATA_DIR)
os.environ["DB_PATH"] = str(_DATA_DIR / "oci-nt.db")
os.environ["BACKUP_DIR"] = str(_DATA_DIR / "backups")

from app.bulk_check_repository import create_job, get_job, request_cancel  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import (  # noqa: E402
    ensure_admin_user,
    get_user_by_username,
    init_database,
    update_user_credentials,
)
from app.instance_cache_repository import (  # noqa: E402
    cached_boot_volume_belongs_to_account,
    cached_private_ip_belongs_to_account,
    get_cached_instance,
    replace_account_instance_cache,
)
from app.security import verify_password  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class V7Phase2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_database()
        init_database()
        ensure_admin_user("admin", "x")

    def test_database_migration_and_background_policy(self) -> None:
        with sqlite3.connect(settings.db_path) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertIn("bulk_check_jobs", tables)
        self.assertIn("oauth_login_codes", tables)
        self.assertIn("launch_jobs", tables)
        self.assertIn("launch_attempts", tables)
        self.assertIn("launch_catalog_snapshots", tables)
        main = (PROJECT_ROOT / "backend/app/main.py").read_text("utf-8")
        self.assertNotIn("account_monitor_scheduler", main)
        self.assertIn("monitor_scheduler_loop", main)  # local host metrics only; no OCI query
        self.assertNotIn("automatic_oci_monitor", main)
        self.assertIn("background_queries\": False", main)

    def test_credentials_allow_any_nonempty_password(self) -> None:
        updated = update_user_credentials(
            "admin", new_username="nt", new_password="1"
        )
        self.assertEqual(updated["username"], "nt")
        stored = get_user_by_username("nt")
        self.assertIsNotNone(stored)
        self.assertTrue(verify_password("1", stored["password_hash"]))
        with self.assertRaises(ValueError):
            update_user_credentials("nt", new_password="")

    def test_bulk_job_is_user_triggered_and_cancellable(self) -> None:
        job = create_job(requested_by="nt", interval_seconds=30, total=3)
        self.assertEqual(job["status"], "PENDING")
        self.assertEqual(job["interval_seconds"], 30)
        self.assertTrue(request_cancel(job["id"]))
        self.assertTrue(get_job(job["id"])["cancel_requested"])

    def test_tenant_cache_ownership_helpers(self) -> None:
        with sqlite3.connect(settings.db_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                """
                INSERT INTO oci_accounts (
                    custom_name, tenancy_ocid, user_ocid, fingerprint,
                    region, private_key_encrypted, account_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "tenant-a", "ocid1.tenancy.oc1..a", "ocid1.user.oc1..a",
                    "aa", "uk-london-1", "encrypted", "PERSONAL_FREE",
                ),
            )
            account_id = int(cursor.lastrowid)
            connection.commit()
        replace_account_instance_cache(
            account_id=account_id,
            account_name="tenant-a",
            account_email="a@example.com",
            instances=[{
                "id": "ocid1.instance.oc1..a",
                "display_name": "N&T",
                "region": "uk-london-1",
                "vnics": [{
                    "id": "ocid1.vnic.oc1..a",
                    "private_ip_id": "ocid1.privateip.oc1..a",
                }],
                "boot_volumes": [{"id": "ocid1.bootvolume.oc1..a"}],
            }],
            errors=[],
            regions_scanned=1,
        )
        self.assertIsNotNone(get_cached_instance(account_id, "ocid1.instance.oc1..a"))
        self.assertTrue(cached_private_ip_belongs_to_account(
            account_id, "ocid1.privateip.oc1..a"
        ))
        self.assertTrue(cached_boot_volume_belongs_to_account(
            account_id, "ocid1.bootvolume.oc1..a"
        ))
        self.assertFalse(cached_private_ip_belongs_to_account(
            account_id, "ocid1.privateip.oc1..other"
        ))

    def test_frontend_has_grouped_oci_start_style_navigation(self) -> None:
        index = (PROJECT_ROOT / "frontend/index.html").read_text("utf-8")
        app_js = (PROJECT_ROOT / "frontend/app.js").read_text("utf-8")
        ui2_js = (PROJECT_ROOT / "frontend/ui2.js").read_text("utf-8")
        for page in ("dashboard", "accounts", "instances", "launch", "cloudflare", "audit", "settings"):
            self.assertIn(f'data-page="{page}"', index)
        for page in ("regions", "network", "storage", "insights", "tools"):
            self.assertNotIn(f'data-page="{page}"', index)
        self.assertNotIn('multiple size="4"', index)
        self.assertIn('data-tenant-tab="instances"', index)
        self.assertIn('data-tenant-tab="launch"', index)
        self.assertIn("OCI 后台查询已关闭", index)
        self.assertNotIn("v4.js", index)
        self.assertIn("/accounts/${account.id}/instances/sync", app_js)
        self.assertIn("/accounts/${account.id}/launch/catalog", app_js)
        self.assertIn("ui2LoadGlobalInstances", ui2_js)
        self.assertIn("ui2LoadGlobalLaunch", ui2_js)
        self.assertIn("API 管理", index)
        self.assertIn("N&amp;T-1", app_js)


    def test_openapi_contains_v7_phase2_routes_without_duplicates(self) -> None:
        import sys
        import types

        class DummyOci(types.ModuleType):
            def __getattr__(self, name):
                value = DummyOci(f"{self.__name__}.{name}")
                setattr(self, name, value)
                return value

        sys.modules.setdefault("oci", DummyOci("oci"))
        from app.main import app

        paths = app.openapi()["paths"]
        expected = {
            "/api/v1/settings/credentials",
            "/api/v1/auth/google/status",
            "/api/v1/auth/google/start",
            "/api/v1/auth/google/callback",
            "/api/v1/auth/google/exchange",
            "/api/v1/settings/account-check",
            "/api/v1/bulk/account-checks",
            "/api/v1/bulk/account-checks/current",
            "/api/v1/bulk/account-checks/{job_id}/cancel",
            "/api/v1/accounts/{account_id}",
            "/api/v1/accounts/{account_id}/instances/sync",
            "/api/v1/accounts/{account_id}/launch/catalog",
            "/api/v1/accounts/{account_id}/launch/catalog/resources",
            "/api/v1/accounts/{account_id}/launch/network/ensure",
            "/api/v1/accounts/{account_id}/launch/jobs",
            "/api/v1/accounts/{account_id}/launch/jobs/{job_id}",
            "/api/v1/accounts/{account_id}/launch/jobs/{job_id}/cancel",
        }
        self.assertTrue(expected.issubset(paths))
        operation_ids = [
            operation["operationId"]
            for item in paths.values()
            for operation in item.values()
            if isinstance(operation, dict) and "operationId" in operation
        ]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))

    def test_tenant_proxy_can_be_configured_during_import(self) -> None:
        index = (PROJECT_ROOT / "frontend/index.html").read_text("utf-8")
        app_js = (PROJECT_ROOT / "frontend/app.js").read_text("utf-8")
        main = (PROJECT_ROOT / "backend/app/main.py").read_text("utf-8")
        self.assertIn('name="proxy_enabled"', index)
        self.assertIn('name="proxy_url"', index)
        self.assertIn('id="proxy-dialog"', index)
        self.assertIn('/proxy/test', app_js)
        self.assertIn('proxy_enabled: bool = Form(False)', main)
        self.assertIn('proxy_url=normalized_proxy_url if proxy_enabled else None', main)
        self.assertIn('save_account_proxy(', main)

    def test_compose_uses_only_test_ports(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text("utf-8")
        nginx = (PROJECT_ROOT / "frontend/nginx.conf").read_text("utf-8")
        dockerfile = (PROJECT_ROOT / "frontend/Dockerfile").read_text("utf-8")
        self.assertIn("network_mode: host", compose)
        self.assertIn("127.0.0.1", compose)
        self.assertIn('"9858"', compose)
        self.assertIn("listen 127.0.0.1:9859", nginx)
        production_port = str(9850 + 7)
        self.assertNotIn(production_port, compose + nginx)
        self.assertNotIn("ports:", compose)
        self.assertIn("COPY app.js", dockerfile)
        self.assertIn("COPY ui2.js", dockerfile)
        self.assertNotIn("v4.js", dockerfile)


if __name__ == "__main__":
    unittest.main(verbosity=2)
