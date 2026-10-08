import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("durable_admission", ROOT / "backend/app/durable_admission.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RepositoryAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY, setting_value TEXT)")
            con.execute("CREATE TABLE manual_tasks(id INTEGER PRIMARY KEY)")
            con.execute("CREATE TABLE launch_jobs(id INTEGER PRIMARY KEY)")

    def test_no_marker_allows(self):
        with sqlite3.connect(self.db) as con:
            con.execute("BEGIN IMMEDIATE")
            module.assert_admission_in_transaction(con)
            con.execute("INSERT INTO manual_tasks VALUES (1)")

    def test_active_marker_rejects_all_task_types(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings VALUES (?, ?)", (module.MAINTENANCE_KEY, json.dumps({"active": True})))
        for table in ("manual_tasks", "launch_jobs"):
            with self.assertRaises(module.DurableMaintenanceBlocked):
                with sqlite3.connect(self.db) as con:
                    con.execute("BEGIN IMMEDIATE")
                    module.assert_admission_in_transaction(con)
                    con.execute(f"INSERT INTO {table} VALUES(1)")
            with sqlite3.connect(self.db) as con:
                self.assertEqual(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_malformed_marker_blocks(self):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings VALUES (?, 'null')", (module.MAINTENANCE_KEY,))
        with sqlite3.connect(self.db) as con:
            con.execute("BEGIN IMMEDIATE")
            with self.assertRaises(module.DurableMaintenanceBlocked):
                module.assert_admission_in_transaction(con)

    def test_without_transaction_is_rejected(self):
        with sqlite3.connect(self.db) as con:
            with self.assertRaises(RuntimeError):
                module.assert_admission_in_transaction(con)


if __name__ == "__main__":
    unittest.main()
