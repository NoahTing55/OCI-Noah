import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location("lease_diag",Path(__file__).resolve().parents[1]/"deploy/oci-lease-diagnostics.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class LeaseDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"app.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT)")
    def set_leases(self, value):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT OR REPLACE INTO system_settings VALUES (?,?)",(mod.KEY,value))
    def test_empty_not_authorized(self):
        r=mod.inspect(self.db)
        self.assertEqual(r["lease_count"],0)
        self.assertFalse(r["release_authorized"])
        self.assertFalse(r["absence_proves_quiescence"])
    def test_outstanding_does_not_disclose_token(self):
        self.set_leases(json.dumps({"my-secret-lease-token":{"started_at":"2026-10-08T16:00:00Z","path":"background:launch:17"}}))
        r=mod.inspect(self.db)
        self.assertEqual(r["lease_count"],1)
        self.assertEqual(r["entries"][0]["kind"],"background")
        self.assertNotIn("my-secret-lease-token",json.dumps(r))
        self.assertFalse(r["automatic_cleanup_allowed"])
    def test_invalid_json_fails_closed(self):
        self.set_leases("broken")
        with self.assertRaises(RuntimeError):
            mod.inspect(self.db)
    def test_incomplete_entry_fails_closed(self):
        self.set_leases(json.dumps({"token":{"path":"background:manual:1"}}))
        with self.assertRaises(RuntimeError):
            mod.inspect(self.db)

if __name__=="__main__":
    unittest.main()
