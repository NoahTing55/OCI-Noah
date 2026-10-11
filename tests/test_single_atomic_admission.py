"""Synthetic SQLite integration test: release maintenance and task admission."""
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

maintenance=load("maintenance_for_atomic_test",ROOT/"deploy/durable_maintenance.py")
admission=load("admission_for_atomic_test",ROOT/"backend/app/durable_admission.py")

class AtomicTaskAdmission(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"test.db"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
              CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,
                  is_secret INTEGER DEFAULT 0,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
              CREATE TABLE manual_tasks(status TEXT);
              CREATE TABLE launch_jobs(status TEXT);
            """)

    def test_maintenance_blocks_subsequent_task_insert(self):
        maintenance.begin(self.db,operator="test",reason="test")
        with sqlite3.connect(self.db,isolation_level=None) as con:
            con.execute("BEGIN IMMEDIATE")
            with self.assertRaises(admission.DurableMaintenanceBlocked):
                admission.assert_admission_in_transaction(con)
            con.execute("ROLLBACK")
        maintenance.end(self.db,operator="test")

    def test_committed_pending_task_blocks_maintenance(self):
        with sqlite3.connect(self.db,isolation_level=None) as con:
            con.execute("BEGIN IMMEDIATE")
            admission.assert_admission_in_transaction(con)
            con.execute("INSERT INTO launch_jobs(status) VALUES('RUNNING')")
            con.execute("COMMIT")
        with self.assertRaisesRegex(RuntimeError,"Undrained launch_jobs"):
            maintenance.begin(self.db,operator="test",reason="test")
        self.assertFalse(maintenance.read_state(self.db)["active"])

    def test_serialized_write_lock_prevents_stale_admission(self):
        with sqlite3.connect(self.db,isolation_level=None) as first:
            first.execute("BEGIN IMMEDIATE")
            admission.assert_admission_in_transaction(first)
            with sqlite3.connect(self.db,timeout=0.01,isolation_level=None) as second:
                with self.assertRaises(sqlite3.OperationalError):
                    second.execute("BEGIN IMMEDIATE")
            first.execute("ROLLBACK")

if __name__=="__main__":
    unittest.main()
