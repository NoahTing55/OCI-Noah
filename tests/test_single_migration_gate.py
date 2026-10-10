import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("single_gate", ROOT / "deploy/single/migration-gate.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class MigrationGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "copy.db"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE schema_version (singleton_id INTEGER, version INTEGER);
                INSERT INTO schema_version VALUES (1,7);
                CREATE TABLE manual_tasks (status TEXT);
                CREATE TABLE launch_jobs (status TEXT);
                CREATE TABLE system_settings (setting_key TEXT);
                CREATE TABLE users (id INTEGER);
            """)

    def tearDown(self):
        self.tmp.cleanup()

    def test_terminal_rows_never_authorize_release(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO launch_jobs VALUES ('SUCCESS')")
        result = mod.inspect_database(self.db)
        self.assertTrue(result["task_rows_terminal"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["single_container_ready"])

    def test_active_task_is_blocked(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO launch_jobs VALUES ('RUNNING')")
        result = mod.inspect_database(self.db)
        self.assertEqual(result["nonterminal_tasks"]["launch_jobs"]["RUNNING"], 1)
        self.assertFalse(result["task_rows_terminal"])

    def test_unsupported_schema_is_blocked(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE schema_version SET version=8")
        with self.assertRaisesRegex(ValueError, "schema"):
            mod.inspect_database(self.db)

    def test_missing_schema_and_symlink_fail_closed(self):
        with sqlite3.connect(self.db) as con:
            con.execute("DROP TABLE system_settings")
        with self.assertRaisesRegex(ValueError, "missing tables"):
            mod.inspect_database(self.db)
        link = Path(self.tmp.name) / "link.db"
        link.symlink_to(self.db)
        with self.assertRaisesRegex(ValueError, "symbolic-link"):
            mod.inspect_database(link)

if __name__ == "__main__":
    unittest.main()
