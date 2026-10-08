import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "durable_maintenance", ROOT / "deploy" / "durable_maintenance.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DurableMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "app.db"
        with sqlite3.connect(self.db) as con:
            con.execute("""CREATE TABLE system_settings(
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT,
                is_secret INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")

    def test_persists_across_connections(self):
        self.assertFalse(module.read_state(self.db)["active"])
        module.begin(self.db, operator="tester", reason="release")
        self.assertTrue(module.read_state(self.db)["active"])
        module.end(self.db, operator="tester")
        self.assertFalse(module.read_state(self.db)["active"])

    def test_cannot_reenter_or_release_inactive(self):
        module.begin(self.db, operator="tester", reason="release")
        with self.assertRaises(RuntimeError):
            module.begin(self.db, operator="other", reason="release")
        module.end(self.db, operator="tester")
        with self.assertRaises(RuntimeError):
            module.end(self.db, operator="tester")

    def test_malformed_marker_blocks(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES(?,?)",
                        (module.KEY, '{"broken":true}'))
        with self.assertRaises(RuntimeError):
            module.read_state(self.db)
        with self.assertRaises(RuntimeError):
            module.begin(self.db, operator="tester", reason="release")

    def test_missing_db_does_not_create(self):
        missing = Path(self.tmp.name) / "none.db"
        with self.assertRaises(FileNotFoundError):
            module.read_state(missing)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
