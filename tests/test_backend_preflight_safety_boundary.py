"""Regression guard: database-only state must never approve API restart."""
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("backend_preflight",ROOT/"deploy/backend-preflight.py")
preflight=importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)

class PreflightBoundaryTests(unittest.TestCase):
    def test_cli_enforces_no_restart_approval(self):
        source=(ROOT/"deploy/backend-preflight.py").read_text()
        self.assertIn('"safe_to_restart": False',source)
        self.assertIn('"release_authorized": False',source)
        self.assertIn('report["db_task_rows_terminal"]',source)
    def test_database_rows_do_not_imply_release(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        db=Path(t.name)/"test.db"
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE manual_tasks(status TEXT)")
            con.execute("CREATE TABLE launch_jobs(status TEXT)")
            con.execute("CREATE TABLE schema_version(singleton_id INTEGER PRIMARY KEY,version INTEGER)")
            con.execute("CREATE TABLE system_upgrade_history(id INTEGER)")
            con.execute("INSERT INTO schema_version VALUES(1,7)")
        observed=preflight.inspect(db)
        self.assertFalse(observed["safe_to_restart"])
        self.assertTrue(observed["db_task_rows_terminal"])
        self.assertFalse(observed["release_authorized"])
        self.assertEqual(observed["nonterminal_tasks"], {"manual_tasks":{}, "launch_jobs":{}})

if __name__=="__main__":
    unittest.main()
