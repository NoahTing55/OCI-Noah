import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location("maintenance",Path(__file__).resolve().parents[1]/"deploy/durable_maintenance.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ExitGuardTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.db=Path(t.name)/"db.sqlite"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,is_secret INTEGER DEFAULT 0,updated_at TEXT)")
            con.execute("CREATE TABLE manual_tasks(id INTEGER,status TEXT)")
            con.execute("CREATE TABLE launch_jobs(id INTEGER,status TEXT)")
        m.begin(self.db,operator="tester",reason="release")

    def test_active_lease_blocks_exit_and_keeps_gate(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES(?,?)",(
                "oci_nt_http_operation_leases_v1",json.dumps({"token":{"path":"background:launch:1","started_at":"now"}})))
        with self.assertRaisesRegex(RuntimeError,"Outstanding OCI leases"):
            m.end(self.db,operator="tester")
        self.assertTrue(m.read_state(self.db)["active"])

    def test_pending_task_blocks_exit(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO launch_jobs VALUES(1,'RUNNING')")
        with self.assertRaisesRegex(RuntimeError,"Undrained launch_jobs"):
            m.end(self.db,operator="tester")
        self.assertTrue(m.read_state(self.db)["active"])

    def test_corrupt_lease_blocks_exit(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES(?,?)",(
                "oci_nt_http_operation_leases_v1", '{"token":null}'))
        with self.assertRaisesRegex(RuntimeError,"lease entry malformed"):
            m.end(self.db,operator="tester")
        self.assertTrue(m.read_state(self.db)["active"])

    def test_clean_exit(self):
        result=m.end(self.db,operator="tester")
        self.assertFalse(result["active"])
        self.assertFalse(m.read_state(self.db)["active"])

if __name__=="__main__":
    unittest.main()
