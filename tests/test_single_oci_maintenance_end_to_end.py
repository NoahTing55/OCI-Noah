"""Isolated SQLite admission tests; no real OCI SDK or production DB access."""
import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

leases=load("release_oci_leases_e2e","backend/app/oci_operation_leases.py")
maintenance=load("release_maintenance_e2e","deploy/durable_maintenance.py")
guard=load("release_route_guard_e2e","backend/app/maintenance_route_guard.py")

class MaintenanceAdmissionE2E(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db=Path(temporary.name)/"isolated.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,is_secret INTEGER DEFAULT 0,updated_at TEXT)")
            con.execute("CREATE TABLE manual_tasks(id INTEGER,status TEXT)")
            con.execute("CREATE TABLE launch_jobs(id INTEGER,status TEXT)")

    def count(self):
        with sqlite3.connect(self.db) as con:
            con.execute("BEGIN IMMEDIATE")
            return leases.active_lease_count_in_transaction(con)

    def test_request_blocked_in_maintenance_then_restored(self):
        path="/api/v1/accounts"
        self.assertTrue(guard.protected_oci_request("POST",path))
        maintenance.begin(self.db,operator="ci",reason="isolated test")
        self.assertTrue(guard.maintenance_active(self.db))
        for name in (path,"/api/v1/instances/sync","background:manual:45","background:launch:81"):
            with self.subTest(name=name):
                with self.assertRaises(leases.OCIAdmissionBlocked):
                    leases.acquire(self.db,name)
        self.assertEqual(self.count(),0)
        maintenance.end(self.db,operator="ci")
        self.assertFalse(guard.maintenance_active(self.db))
        with leases.admitted_oci_operation(self.db,path):
            self.assertEqual(self.count(),1)
        self.assertEqual(self.count(),0)

    def test_inflight_request_refuses_maintenance_until_release(self):
        started=threading.Event()
        finish=threading.Event()
        failures=[]
        def simulated_request():
            try:
                with leases.admitted_oci_operation(self.db,"/api/v1/accounts/import-preview"):
                    started.set()
                    if not finish.wait(timeout=6):
                        raise TimeoutError("synthetic request stuck")
            except BaseException as exc:
                failures.append(str(exc))
        thread=threading.Thread(target=simulated_request)
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            self.assertEqual(self.count(),1)
            with self.assertRaises(RuntimeError):
                maintenance.begin(self.db,operator="ci",reason="inflight")
            self.assertEqual(self.count(),1)
        finally:
            finish.set()
            thread.join(timeout=7)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures,failures)
        self.assertEqual(self.count(),0)
        maintenance.begin(self.db,operator="ci",reason="now idle")
        self.assertTrue(guard.maintenance_active(self.db))
        maintenance.end(self.db,operator="ci")

    def test_corrupt_registry_never_grants_admission_or_maintenance(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES(?,?)",
                        (leases.LEASES,"not-json"))
        with self.assertRaises(leases.OCIAdmissionBlocked):
            leases.acquire(self.db,"/api/v1/instances/sync")
        with self.assertRaises(RuntimeError):
            maintenance.begin(self.db,operator="ci",reason="corrupt registry")

if __name__=="__main__":
    unittest.main()
