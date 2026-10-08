import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "maintenance_route_guard", ROOT / "backend/app/maintenance_route_guard.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RouteGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "app.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY, setting_value TEXT)")

    def set_marker(self, value):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT OR REPLACE INTO system_settings VALUES (?,?)", (module.KEY, value))

    def test_relevant_oci_routes_protected(self):
        routes = [
            "/api/v1/accounts/5/instances/ocid/actions",
            "/api/v1/accounts/5/launch/network/ensure",
            "/api/v1/accounts/5/launch/jobs",
            "/api/v1/tasks/instance-batch",
            "/api/v1/bulk/account-checks",
            "/api/v1/instances/sync",
            "/api/v1/proxy-allocation/execute",
        ]
        for path in routes:
            with self.subTest(path=path):
                self.assertTrue(module.protected_oci_write("POST", path))

    def test_login_and_reads_not_blocked(self):
        self.assertFalse(module.protected_oci_write("POST", "/api/v1/auth/login"))
        self.assertFalse(module.protected_oci_write("GET", "/api/v1/tasks/instance-batch"))
        self.assertFalse(module.protected_oci_write("POST", "/api/v1/system/backups"))

    def test_active_and_inactive_marker(self):
        self.assertFalse(module.maintenance_active(self.db))
        self.set_marker('{"active":true}')
        self.assertTrue(module.maintenance_active(self.db))
        self.set_marker('{"active":false}')
        self.assertFalse(module.maintenance_active(self.db))

    def test_corrupt_marker_refused(self):
        self.set_marker('{"unexpected":1}')
        with self.assertRaises(RuntimeError):
            module.maintenance_active(self.db)

    def test_missing_db_fail_closed(self):
        with self.assertRaises(RuntimeError):
            module.maintenance_active(Path(self.tmp.name) / "missing.db")


if __name__ == "__main__":
    unittest.main()
