"""Integration tests for atomic maintenance vs OCI HTTP/background leases.

Disposable SQLite only. No OCI network, Docker, production data, or credentials.
"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from app.oci_operation_leases import acquire, release, OCIAdmissionBlocked

spec=importlib.util.spec_from_file_location("test_maintenance_module",ROOT/"deploy/durable_maintenance.py")
maintenance=importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)

class LeaseMaintenanceIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"test.sqlite"
        with sqlite3.connect(self.db) as db:
            db.executescript("""
                CREATE TABLE system_settings (
                    setting_key TEXT PRIMARY KEY,setting_value TEXT,
                    is_secret INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE manual_tasks(status TEXT);
                CREATE TABLE launch_jobs(status TEXT);
            """)

    def test_existing_http_lease_refuses_maintenance(self):
        token=acquire(self.db,"/api/v1/instances/action")
        with self.assertRaisesRegex(RuntimeError,"still active"):
            maintenance.begin(self.db,operator="ci",reason="release")
        release(self.db,token)
        self.assertFalse(maintenance.read_state(self.db)["active"])

    def test_maintenance_blocks_http_and_background_work(self):
        maintenance.begin(self.db,operator="ci",reason="release")
        for path in ("/api/v1/instances/action","background:launch:12","background:launch_notification:12"):
            with self.subTest(path=path):
                with self.assertRaises(OCIAdmissionBlocked):
                    acquire(self.db,path)
        maintenance.end(self.db,operator="ci")
        token=acquire(self.db,"background:launch:12")
        release(self.db,token)

    def test_competing_admission_and_maintenance_serialize(self):
        # Two independent SQLite connections race; BEGIN IMMEDIATE must
        # serialize the operations without a gap permitting new OCI calls.
        import threading
        from concurrent.futures import ThreadPoolExecutor
        for i in range(12):
            with self.subTest(round=i):
                with sqlite3.connect(self.db) as con:
                    con.execute("DELETE FROM system_settings")
                barrier=threading.Barrier(2)
                def start_lease():
                    barrier.wait()
                    try:
                        return ("lease",acquire(self.db,"background:race"))
                    except OCIAdmissionBlocked:
                        return ("blocked",None)
                def start_maintenance():
                    barrier.wait()
                    try:
                        maintenance.begin(self.db,operator="ci",reason="race")
                        return "maintenance_started"
                    except RuntimeError:
                        return "maintenance_blocked"
                with ThreadPoolExecutor(max_workers=2) as pool:
                    a=pool.submit(start_lease)
                    b=pool.submit(start_maintenance)
                    lease_kind,token=a.result(timeout=15)
                    maint=b.result(timeout=15)
                self.assertIn((lease_kind,maint),(
                    ("lease","maintenance_blocked"),
                    ("blocked","maintenance_started")))
                if token:
                    release(self.db,token)
                if maint=="maintenance_started":
                    self.assertTrue(maintenance.read_state(self.db)["active"])
                    maintenance.end(self.db,operator="ci")

    def test_malformed_maintenance_marker_blocks_new_lease(self):
        for raw in ('not-json','[]','{"active":"false"}','{"active":null}'):
            with self.subTest(raw=raw),sqlite3.connect(self.db) as con:
                con.execute("DELETE FROM system_settings")
                con.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES (?,?)",
                            ("oci_nt_release_maintenance_v1",raw))
            with self.assertRaises(OCIAdmissionBlocked):
                acquire(self.db,"background:malformed")

    def test_maintenance_cannot_end_with_lease(self):
        maintenance.begin(self.db,operator="ci",reason="release")
        # Deliberate synthetic leaked lease to verify fail-closed reopening.
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES (?,?)",
                        ("oci_nt_http_operation_leases_v1",json.dumps({
                           "synthetic":{"started_at":"2026-01-01T00:00:00Z","path":"background:launch:99"}})))
        with self.assertRaisesRegex(RuntimeError,"Outstanding OCI leases"):
            maintenance.end(self.db,operator="ci")
        self.assertTrue(maintenance.read_state(self.db)["active"])

if __name__=="__main__":
    unittest.main()
