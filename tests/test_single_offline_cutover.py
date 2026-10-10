import hashlib
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "deploy/single/offline-cutover-drill.py"
spec = importlib.util.spec_from_file_location("single_offline_drill", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class OfflineCutoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "snapshot.db"
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE schema_version (singleton_id INTEGER PRIMARY KEY, version INTEGER)")
            db.execute("INSERT INTO schema_version VALUES (1, 7)")
            db.execute("CREATE TABLE users (id INTEGER, username TEXT)")
            db.execute("INSERT INTO users VALUES (1, 'synthetic')")
        self.hash = mod.checksum(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_disposable_forward_and_rollback_copies(self):
        result = mod.rehearse(self.path, self.hash)
        self.assertEqual(result["result"], "OFFLINE_DATA_REHEARSAL_OK")
        self.assertTrue(result["source_unchanged"])
        self.assertFalse(result["production_switch_authorized"])
        self.assertFalse(result["runtime_task_drain_verified"])
        self.assertFalse(result["image_rollback_verified"])
        self.assertEqual(self.hash, mod.checksum(self.path))

    def test_tampering_fails_closed(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO users VALUES (2, 'changed')")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            mod.rehearse(self.path, self.hash)

    def test_schema_mismatch_fails_closed(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE schema_version SET version=8")
        with self.assertRaisesRegex(ValueError, "schema"):
            mod.rehearse(self.path, mod.checksum(self.path))

    def test_missing_file_and_symlink_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "regular file"):
            mod.rehearse(Path(self.tmp.name)/"missing.db", self.hash)
        link = Path(self.tmp.name) / "alias.db"
        link.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "regular file"):
            mod.rehearse(link, self.hash)


if __name__ == "__main__":
    unittest.main()
