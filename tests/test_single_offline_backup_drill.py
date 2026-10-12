import importlib.util
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/single/offline-backup-drill.py"
spec = importlib.util.spec_from_file_location("offline_backup_drill",SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class OfflineBackupDrill(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.src=self.root/"synthetic.db"
        self.out=self.root/"verified.db"
        with sqlite3.connect(self.src) as db:
            db.execute("CREATE TABLE schema_version (singleton_id INTEGER PRIMARY KEY, version INTEGER)")
            db.execute("INSERT INTO schema_version VALUES (1,7)")
            db.execute("CREATE TABLE synthetic (item TEXT)")
            db.execute("INSERT INTO synthetic VALUES ('harmless-fixture')")
    def test_verified_backup_is_private_and_durable(self):
        result=module.drill(self.src,self.out)
        self.assertEqual(result["status"],"DISPOSABLE_BACKUP_VERIFIED")
        self.assertEqual(result["sha256"],module.digest(self.out))
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode),0o600)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["source_stopped_attested"])
        self.assertFalse(result["credential_compatibility_verified"])
        with sqlite3.connect(self.out) as db:
            self.assertEqual(db.execute("SELECT item FROM synthetic").fetchone()[0],"harmless-fixture")
    def test_never_overwrites_existing_backup(self):
        self.out.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            module.drill(self.src,self.out)
        self.assertEqual(self.out.read_bytes(),b"keep")
    def test_rejects_non_schema7(self):
        with sqlite3.connect(self.src) as db:
            db.execute("UPDATE schema_version SET version=6")
        with self.assertRaises(RuntimeError):
            module.drill(self.src,self.out)
        self.assertFalse(self.out.exists())
    def test_rejects_symlink_source(self):
        link=self.root/"pointer.db"
        link.symlink_to(self.src)
        with self.assertRaises(ValueError):
            module.drill(link,self.out)
        self.assertFalse(self.out.exists())

if __name__=="__main__": unittest.main()
