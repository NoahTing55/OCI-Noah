"""Read-only backup comparison must never confuse old verified backup with a fresh recovery point."""
import importlib.util
import hashlib
from pathlib import Path
from unittest.mock import patch
import tempfile
import sqlite3
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("production_audit_backup_freshness",ROOT/"deploy/single/production-readonly-audit.py")
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

def make_db(path,count):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE oci_accounts(id INTEGER PRIMARY KEY)")
        db.executemany("INSERT INTO oci_accounts(id) VALUES (?)",[(i,) for i in range(count)])
        db.execute("CREATE TABLE schema_version(singleton_id INTEGER PRIMARY KEY, version INTEGER)")
        db.execute("INSERT INTO schema_version VALUES(1,7)")

class BackupFreshness(unittest.TestCase):
    def test_backup_account_count_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"snapshot.db"
            make_db(p,27)
            self.assertEqual(audit.database_account_count(p),27)

    def test_verified_historical_snapshot_cannot_authorize_release(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"snapshot.db"
            make_db(p,27)
            dummy=[{"running":True,"health":"healthy","compose_project":"oci-nt"}]*4
            with patch.object(audit,"docker_inventory",return_value=dummy), \
                 patch.object(audit,"database_inventory",return_value={"schema":7,"accounts":26}), \
                 patch.object(audit,"runtime_process_inventory",return_value=[]), \
                 patch.object(audit.os,"stat",return_value=type("Stat",(),{"st_uid":0,"st_gid":997,"st_mode":0o660})()), \
                 patch.object(audit,"EXPECTED_BACKUP_SHA",hashlib.sha256(p.read_bytes()).hexdigest()):
                result=audit.report(root=audit.ROOT,backup=p)
            self.assertTrue(result["backup"]["sha256_match"])
            self.assertEqual(result["backup"]["accounts"],27)
            self.assertFalse(result["backup"]["account_count_matches_live"])
            self.assertFalse(result["backup"]["current_recovery_point_proven"])
            self.assertFalse(result["production_cutover_authorized"])

if __name__=="__main__":
    unittest.main()
