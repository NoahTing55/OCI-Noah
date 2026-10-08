import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location("maint",Path(__file__).resolve().parents[1]/"deploy/durable_maintenance.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class EntryLeaseValidationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/"db.sqlite"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,is_secret INTEGER DEFAULT 0,updated_at TEXT)")
            con.execute("CREATE TABLE manual_tasks(id INTEGER,status TEXT)")
            con.execute("CREATE TABLE launch_jobs(id INTEGER,status TEXT)")
    def test_corrupt_record_fails_closed(self):
        for payload in ('{"token":null}','{"token":{}}','{"token":{"path":"x"}}','{"":{"path":"x","started_at":"now"}}'):
            with self.subTest(payload=payload):
                with sqlite3.connect(self.db) as con:
                    con.execute("INSERT OR REPLACE INTO system_settings(setting_key,setting_value) VALUES(?,?)",("oci_nt_http_operation_leases_v1",payload))
                with self.assertRaisesRegex(RuntimeError,"lease entry malformed"):
                    m.begin(self.db,operator="tester",reason="release")
                self.assertFalse(m.read_state(self.db)["active"])
    def test_empty_registry_allows_entry(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES(?,?)",("oci_nt_http_operation_leases_v1","{}"))
        m.begin(self.db,operator="tester",reason="release")
        self.assertTrue(m.read_state(self.db)["active"])

if __name__=="__main__":
    unittest.main()
