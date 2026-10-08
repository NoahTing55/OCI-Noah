import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("drain", ROOT / "deploy/release-drain-check.py")
drain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drain)


class DrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "app.db"
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY, setting_value TEXT)")
            c.execute("CREATE TABLE manual_tasks(id INTEGER, status TEXT)")
            c.execute("CREATE TABLE launch_jobs(id INTEGER, status TEXT)")

    def set_gate(self, value):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT OR REPLACE INTO system_settings VALUES (?, ?)", (drain.KEY, value))

    def test_requires_persistent_gate(self):
        result = drain.report(self.db)
        self.assertFalse(result["db_task_drain_ready"])
        self.assertFalse(result["release_authorized"])

    def test_empty_when_gate_active(self):
        self.set_gate('{"active":true}')
        result = drain.report(self.db)
        self.assertTrue(result["db_task_drain_ready"])
        self.assertFalse(result["release_authorized"])

    def test_outstanding_http_lease_blocks(self):
        self.set_gate('{"active":true}')
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO system_settings VALUES (?,?)", (
                drain.LEASE_KEY,
                json.dumps({"opaque-http-token": {"path": "/api/v1/instances/1/actions", "started_at": "now"}}),
            ))
        result = drain.report(self.db)
        self.assertEqual(result["oci_operation_leases"], 1)
        self.assertFalse(result["db_task_drain_ready"])
        self.assertFalse(result["release_authorized"])

    def test_outstanding_background_lease_blocks(self):
        self.set_gate('{"active":true}')
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO system_settings VALUES (?,?)", (
                drain.LEASE_KEY,
                json.dumps({"opaque-background-token": {"path": "background:launch:17", "started_at": "now"}}),
            ))
        self.assertFalse(drain.report(self.db)["db_task_drain_ready"])

    def test_malformed_lease_record_fails_closed(self):
        self.set_gate('{"active":true}')
        for payload in ("bad-json", "[]", '{"token":null}'):
            with self.subTest(payload=payload):
                with sqlite3.connect(self.db) as c:
                    c.execute("INSERT OR REPLACE INTO system_settings VALUES (?,?)", (drain.LEASE_KEY, payload))
                with self.assertRaises(RuntimeError):
                    drain.report(self.db)

    def test_pending_launch_blocks(self):
        self.set_gate('{"active":true}')
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO launch_jobs VALUES (1,'WAITING')")
        self.assertFalse(drain.report(self.db)["db_task_drain_ready"])

    def test_unknown_task_blocks(self):
        self.set_gate('{"active":true}')
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO manual_tasks VALUES (1,'UNRECOGNIZED')")
        self.assertFalse(drain.report(self.db)["db_task_drain_ready"])

    def test_malformed_marker_blocks(self):
        self.set_gate(json.dumps({"other": True}))
        with self.assertRaises(RuntimeError):
            drain.report(self.db)


if __name__ == "__main__":
    unittest.main()
