import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("single_audit", ROOT/"deploy/single/readiness-audit.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SingleAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name)/"live.db"
        self.snapshot = Path(self.tmp.name)/"snapshot.db"
        with sqlite3.connect(self.live) as db:
            db.executescript("""
                CREATE TABLE schema_version (singleton_id INTEGER, version INTEGER);
                INSERT INTO schema_version VALUES (1,7);
                CREATE TABLE manual_tasks (status TEXT);
                CREATE TABLE launch_jobs (status TEXT);
                CREATE TABLE users (id INTEGER);
                CREATE TABLE system_settings (setting_key TEXT, setting_value TEXT);
            """)
        with sqlite3.connect(self.live) as src, sqlite3.connect(self.snapshot) as dst:
            src.backup(dst)
        self.digest = module.sha256(self.snapshot)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_gate_does_not_authorize_release(self):
        report = module.audit(self.live, self.snapshot, self.digest)
        self.assertFalse(report["persisted_drain_indicates_ready"])
        self.assertFalse(report["production_switch_authorized"])
        self.assertEqual(module.sha256(self.snapshot), self.digest)

    def test_active_lease_blocks_recorded_drain(self):
        with sqlite3.connect(self.live) as db:
            db.execute("INSERT INTO system_settings VALUES (?,?)",(
                "oci_nt_release_maintenance_v1", '{"active":true}'
            ))
            db.execute("INSERT INTO system_settings VALUES (?,?)",(
                "oci_nt_http_operation_leases_v1",
                '{"test":{"path":"background:launch","started_at":"2026-10-10T00:00:00Z"}}'
            ))
        report = module.audit(self.live, self.snapshot, self.digest)
        self.assertEqual(report["evidence"]["drain"]["oci_operation_leases"], 1)
        self.assertFalse(report["persisted_drain_indicates_ready"])
        self.assertFalse(report["production_switch_authorized"])

    def test_positive_persisted_evidence_is_still_not_authorization(self):
        with sqlite3.connect(self.live) as db:
            db.execute("INSERT INTO system_settings VALUES (?,?)",(
                "oci_nt_release_maintenance_v1", '{"active":true}'
            ))
        report = module.audit(self.live, self.snapshot, self.digest)
        self.assertTrue(report["persisted_drain_indicates_ready"])
        self.assertFalse(report["production_switch_authorized"])

    def test_tampering_and_live_path_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "must not"):
            module.audit(self.live, self.live, module.sha256(self.live))
        with self.assertRaisesRegex(ValueError, "checksum"):
            module.audit(self.live, self.snapshot, "0"*64)

if __name__ == "__main__":
    unittest.main()
