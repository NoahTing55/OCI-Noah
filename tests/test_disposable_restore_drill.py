import hashlib
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location("drill",Path(__file__).resolve().parents[1]/"deploy/disposable-restore-drill.py")
tool=importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

class DisposableRestoreTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.snapshot=Path(temp.name)/"snapshot.db"
        with sqlite3.connect(self.snapshot) as con:
            con.execute("CREATE TABLE schema_version(singleton_id INTEGER PRIMARY KEY,version INTEGER)")
            con.execute("INSERT INTO schema_version VALUES(1,7)")
            con.execute("CREATE TABLE manual_tasks(id INTEGER,status TEXT)")
            con.execute("INSERT INTO manual_tasks VALUES(1,'COMPLETED')")

    def test_restore_preserves_original(self):
        before=tool.sha(self.snapshot)
        r=tool.drill(self.snapshot,before)
        self.assertEqual(r["result"],"DISPOSABLE_RESTORE_VERIFIED")
        self.assertEqual(r["schema"],7)
        self.assertEqual(tool.sha(self.snapshot),before)
        self.assertFalse(r["release_authorized"])
        self.assertFalse(r["old_image_rollback_verified"])

    def test_checksum_mismatch_refuses(self):
        with self.assertRaises(RuntimeError):
            tool.drill(self.snapshot,"0"*64)

    def test_invalid_checksum_refuses(self):
        with self.assertRaises(ValueError):
            tool.drill(self.snapshot,"not a digest")

if __name__=="__main__":
    unittest.main()
