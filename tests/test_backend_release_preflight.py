import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_preflight", ROOT / "deploy" / "backend-preflight.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class BackendPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.db"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
            CREATE TABLE schema_version(singleton_id integer, version integer);
            INSERT INTO schema_version VALUES(1, 7);
            CREATE TABLE system_upgrade_history(id integer);
            CREATE TABLE manual_tasks(id integer, status text);
            CREATE TABLE launch_jobs(id integer, status text);
            """)

    def test_idle(self):
        self.assertTrue(module.inspect(self.db)["safe_to_restart"])

    def test_manual_running_blocks(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO manual_tasks VALUES(1,'RUNNING')")
        result = module.inspect(self.db)
        self.assertFalse(result["safe_to_restart"])
        self.assertEqual(result["nonterminal_tasks"]["manual_tasks"]["RUNNING"], 1)

    def test_launch_pending_blocks(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO launch_jobs VALUES(1,'PENDING')")
        self.assertFalse(module.inspect(self.db)["safe_to_restart"])

    def test_unknown_status_blocks(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO manual_tasks VALUES(1,'SOMETHING_NEW')")
        self.assertFalse(module.inspect(self.db)["safe_to_restart"])

    def test_missing_table_blocks(self):
        with sqlite3.connect(self.db) as con:
            con.execute("DROP TABLE launch_jobs")
        with self.assertRaises(RuntimeError):
            module.inspect(self.db)

if __name__ == "__main__":
    unittest.main()
