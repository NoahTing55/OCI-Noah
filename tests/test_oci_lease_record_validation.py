import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("oci_operation_leases",ROOT/"backend/app/oci_operation_leases.py")
leases=importlib.util.module_from_spec(spec)
spec.loader.exec_module(leases)

class LeaseRecordValidationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"db.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,is_secret INTEGER DEFAULT 0,updated_at TEXT)")
    def set_corrupt_registry(self, value):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT OR REPLACE INTO system_settings(setting_key,setting_value) VALUES(?,?)",(leases.LEASES,value))
    def test_no_new_lease_when_existing_entry_corrupted(self):
        for value in ('{"token":null}', '{"token":{}}', '{"token":{"path":"x"}}', '{"token":{"path":"","started_at":"now"}}'):
            with self.subTest(value=value):
                self.set_corrupt_registry(value)
                with self.assertRaises(leases.OCIAdmissionBlocked):
                    leases.acquire(self.db,"/api/v1/instances/actions")
                with sqlite3.connect(self.db) as c:
                    original=c.execute("SELECT setting_value FROM system_settings WHERE setting_key=?",(leases.LEASES,)).fetchone()[0]
                self.assertEqual(original,value)
    def test_invalid_registry_rejects_release(self):
        self.set_corrupt_registry('{"token":{"started_at":"now","path":"x"},"bad":false}')
        with self.assertRaises(leases.OCIAdmissionBlocked):
            leases.release(self.db,"token")
    def test_valid_lease_round_trip(self):
        token=leases.acquire(self.db,"/api/v1/instances/actions")
        self.assertEqual(leases.active_lease_count_in_transaction(sqlite3.connect(self.db)),1)
        leases.release(self.db,token)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(leases.active_lease_count_in_transaction(c),0)

if __name__=="__main__":
    unittest.main()
