import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

leases = load("oci_leases", "backend/app/oci_operation_leases.py")
maintenance = load("durable_maintenance", "deploy/durable_maintenance.py")


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name)/"db.sqlite"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,is_secret INTEGER DEFAULT 0,updated_at TEXT)")

    def test_lease_prevents_maintenance_until_finished(self):
        token = leases.acquire(self.db, "/api/v1/accounts/1/instances/x/actions")
        with self.assertRaises(RuntimeError):
            maintenance.begin(self.db, operator="tester", reason="upgrade")
        leases.release(self.db, token)
        maintenance.begin(self.db, operator="tester", reason="upgrade")
        with self.assertRaises(leases.OCIAdmissionBlocked):
            leases.acquire(self.db, "/api/v1/instances/sync")
        maintenance.end(self.db, operator="tester")

    def test_context_cleans_up_after_error(self):
        with self.assertRaises(ValueError):
            with leases.admitted_oci_operation(self.db, "/api/v1/instances/sync"):
                raise ValueError("failed OCI request")
        self.assertEqual(self._count(), 0)

    def _count(self):
        with sqlite3.connect(self.db) as con:
            con.execute("BEGIN IMMEDIATE")
            return leases.active_lease_count_in_transaction(con)

    def test_parallel_requests_are_all_tracked(self):
        barrier=threading.Barrier(3)
        done=threading.Event()
        errors=[]
        def worker():
            try:
                with leases.admitted_oci_operation(self.db, "/api/v1/instances/sync"):
                    barrier.wait(timeout=6)
                    if not done.wait(6):
                        raise TimeoutError("wait")
            except BaseException as exc:
                errors.append(str(exc))
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        try:
            barrier.wait(timeout=6)
            self.assertEqual(self._count(), 2)
            with self.assertRaises(RuntimeError):
                maintenance.begin(self.db, operator="operator", reason="release")
        finally:
            done.set()
            for t in threads: t.join(7)
        self.assertEqual(errors, [])
        self.assertEqual(self._count(), 0)

    def test_malformed_lease_row_blocks_maintenance(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES(?,?)",(leases.LEASES, "broken"))
        with self.assertRaises(RuntimeError):
            maintenance.begin(self.db, operator="tester", reason="upgrade")


if __name__=="__main__":
    unittest.main()
