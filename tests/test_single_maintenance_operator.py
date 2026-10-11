import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("maintenance_operator",ROOT/"deploy/single/maintenance-operator.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class OperatorMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"db.sqlite"
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,is_secret INTEGER DEFAULT 0,updated_at TEXT)")
            c.execute("CREATE TABLE manual_tasks(status TEXT)")
            c.execute("CREATE TABLE launch_jobs(status TEXT)")

    def test_status_and_explicit_begin_end(self):
        self.assertFalse(m.execute("status",self.db)["maintenance_active"])
        with self.assertRaises(RuntimeError):
            m.execute("begin",self.db,operator="test",reason="release")
        state=m.execute("begin",self.db,operator="test",reason="release",confirmation="BEGIN OCI-N&T MAINTENANCE")
        self.assertTrue(state["maintenance_active"])
        self.assertFalse(state["release_authorized"])
        with self.assertRaises(RuntimeError):
            m.execute("end",self.db,operator="test")
        state=m.execute("end",self.db,operator="test",confirmation="END OCI-N&T MAINTENANCE")
        self.assertFalse(state["maintenance_active"])

    def test_active_task_prevents_begin(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO launch_jobs(status) VALUES ('RUNNING')")
        with self.assertRaises(RuntimeError):
            m.execute("begin",self.db,operator="test",reason="release",confirmation="BEGIN OCI-N&T MAINTENANCE")
        self.assertFalse(m.execute("status",self.db)["maintenance_active"])

    def test_existing_lease_prevents_begin(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES (?,?)",("oci_nt_http_operation_leases_v1",'{"worker":{"started_at":"now","path":"background:launch"}}'))
        with self.assertRaises(RuntimeError):
            m.execute("begin",self.db,operator="test",reason="release",confirmation="BEGIN OCI-N&T MAINTENANCE")
        self.assertFalse(m.execute("status",self.db)["maintenance_active"])

if __name__=="__main__":
    unittest.main()
