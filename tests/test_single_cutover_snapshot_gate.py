import hashlib
import importlib.util
import sqlite3
import tempfile
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/"deploy/single/cutover-snapshot-gate.py"
spec=importlib.util.spec_from_file_location("snapshot_gate",path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class SnapshotGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"offline.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE schema_version(singleton_id INTEGER,version INTEGER)")
            conn.execute("INSERT INTO schema_version VALUES(1,7)")
            conn.execute("CREATE TABLE oci_accounts(id INTEGER)")
            conn.execute("INSERT INTO oci_accounts VALUES(1)")
        self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest()
    def tearDown(self): self.tmp.cleanup()
    def test_valid_snapshot_never_authorizes_cutover(self):
        result=mod.verify(self.db,self.sha,1)
        self.assertEqual(result["status"],"SNAPSHOT_CONTENT_VERIFIED_PLAN_ONLY")
        self.assertFalse(result["cutover_point_consistency_proven"])
        self.assertFalse(result["production_cutover_authorized"])
        self.assertFalse(result["snapshot_restore_rehearsed"])
    def test_mismatched_sha_denied(self):
        with self.assertRaises(ValueError):mod.verify(self.db,"0"*64,1)
    def test_mismatched_accounts_denied(self):
        with self.assertRaises(ValueError):mod.verify(self.db,self.sha,2)
    def test_corruption_denied(self):
        self.db.write_bytes(b"corrupted")
        sha=hashlib.sha256(self.db.read_bytes()).hexdigest()
        with self.assertRaises(sqlite3.DatabaseError):mod.verify(self.db,sha,1)
    def test_symlink_denied(self):
        alias=Path(self.tmp.name)/"alias.db"
        alias.symlink_to(self.db)
        with self.assertRaises(ValueError):mod.verify(alias,self.sha,1)
if __name__=="__main__":unittest.main()
