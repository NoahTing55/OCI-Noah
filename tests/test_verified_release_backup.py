import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verified_release_backup", ROOT / "deploy" / "verified-release-backup.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "source.db"
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE schema_version(singleton_id INTEGER, version INTEGER)")
            c.execute("INSERT INTO schema_version VALUES(1,7)")
            c.execute("CREATE TABLE example(value TEXT)")
            c.execute("INSERT INTO example VALUES('preserved')")

    def test_verified_snapshot(self):
        report = module.snapshot(self.db, self.root / "snapshots")
        self.assertEqual(report["result"], "VERIFIED_SNAPSHOT")
        self.assertEqual(report["schema"], 7)
        dest = Path(report["file"])
        self.assertTrue(dest.exists())
        self.assertEqual(report["sha256"], module.sha256(dest))
        self.assertTrue(Path(str(dest) + ".json").exists())
        with sqlite3.connect(dest) as con:
            self.assertEqual(con.execute("SELECT value FROM example").fetchone()[0], "preserved")
            self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_missing_source_rejected(self):
        with self.assertRaises(RuntimeError):
            module.snapshot(self.root / "missing.db", self.root / "snapshots")


if __name__ == "__main__":
    unittest.main()
