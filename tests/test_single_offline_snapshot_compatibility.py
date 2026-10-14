import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("snapshot_gate",ROOT/"deploy/single/offline-snapshot-compatibility.py")
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

class OfflineSnapshotGate(unittest.TestCase):
    def test_explicit_snapshot_validates_without_release_approval(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"snapshot.sqlite"
            with sqlite3.connect(p) as con:
                con.execute("CREATE TABLE schema_version(singleton_id INTEGER PRIMARY KEY, version INTEGER)")
                con.execute("INSERT INTO schema_version VALUES(1,7)")
                con.execute("CREATE TABLE oci_accounts(id INTEGER PRIMARY KEY)")
                con.executemany("INSERT INTO oci_accounts VALUES (?)",[(1,),(2,)])
            sha=hashlib.sha256(p.read_bytes()).hexdigest()
            result=gate.inspect_snapshot(p,sha,2)
            self.assertEqual(result["schema"],7)
            self.assertFalse(result["production_cutover_authorized"])
            self.assertFalse(result["credentials_decrypted"])
            self.assertFalse(result["candidate_image_verified"])
            with self.assertRaises(ValueError):
                gate.inspect_snapshot(p,sha,3)
            with self.assertRaises(ValueError):
                gate.inspect_snapshot(p,"0"*64,2)
            with self.assertRaises(ValueError):
                gate.inspect_snapshot(p,"broken",2)
            link=Path(d)/"link.sqlite"
            link.symlink_to(p)
            with self.assertRaises(ValueError):
                gate.inspect_snapshot(link,sha,2)

    def test_live_path_rejected(self):
        with self.assertRaises(ValueError):
            gate.inspect_snapshot(Path("/opt/oci-nt/data/oci-nt.db"),"0"*64,26)

if __name__=="__main__":
    unittest.main()
