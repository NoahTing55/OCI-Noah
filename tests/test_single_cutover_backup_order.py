"""No candidate can start before stopped-db backup integrity and old-release check."""
import importlib.util
from pathlib import Path
import unittest

PATH=Path(__file__).resolve().parents[1]/"deploy/single/cutover-transaction.py"
spec=importlib.util.spec_from_file_location("cutover_backup_order",PATH)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class Fake:
    def __init__(self,fail=None):
        self.called=[]
        self.fail=fail
    def __getattr__(self,name):
        def operation():
            self.called.append(name)
            if name in (self.fail if isinstance(self.fail, (set, tuple, list)) else (self.fail,)):
                raise RuntimeError("injected failure")
        return operation

class BackupOrdering(unittest.TestCase):
    def test_backup_proof_precedes_candidate_boot(self):
        ops=Fake()
        status=mod.coordinate(ops,trusted_test_context=True)
        self.assertEqual(status["status"],"SINGLE_HEALTHY_MAINTENANCE_HELD")
        self.assertLess(ops.called.index("backup_stopped_database"),
                        ops.called.index("verify_stopped_backup_integrity"))
        self.assertLess(ops.called.index("verify_stopped_backup_integrity"),
                        ops.called.index("verify_backup_old_release_readability"))
        self.assertLess(ops.called.index("verify_backup_old_release_readability"),
                        ops.called.index("start_single"))
    def test_bad_backup_never_boots_candidate(self):
        ops=Fake("verify_stopped_backup_integrity")
        result=mod.coordinate(ops,trusted_test_context=True)
        self.assertEqual(result["status"],"FOUR_RESTORED_MAINTENANCE_HELD")
        self.assertFalse(result["stopped_backup_verified"])
        self.assertNotIn("start_single",ops.called)
        self.assertEqual(ops.called[-4:],[
            "preserve_failed_candidate_database","verify_database_for_old_release",
            "restore_four_pinned","verify_four_healthy"])
    def test_incompatible_old_release_blocks_candidate(self):
        ops=Fake("verify_backup_old_release_readability")
        result=mod.coordinate(ops,trusted_test_context=True)
        self.assertEqual(result["status"],"FOUR_RESTORED_MAINTENANCE_HELD")
        self.assertNotIn("start_single",ops.called)
        self.assertFalse(result["admission_reopened"])
    def test_rollback_check_failure_never_restarts_old(self):
        ops=Fake({"verify_single", "verify_database_for_old_release"})
        with self.assertRaises(mod.RecoveryFailed):
            mod.coordinate(ops,trusted_test_context=True)
        self.assertNotIn("restore_four_pinned",ops.called)
    def test_no_production_execution(self):
        with self.assertRaises(mod.CutoverBlocked):
            mod.coordinate(Fake())
if __name__=="__main__":
    unittest.main()
