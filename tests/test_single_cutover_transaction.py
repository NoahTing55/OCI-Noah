import importlib.util
import unittest
from pathlib import Path

P=Path(__file__).resolve().parents[1]/"deploy/single/cutover-transaction.py"
spec=importlib.util.spec_from_file_location("cutover_transaction",P)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class MockOps:
    def __init__(self,fail=None):
        self.calls=[]
        self.fail=fail
    def __getattr__(self,name):
        def perform():
            self.calls.append(name)
            if name==self.fail:
                raise RuntimeError("injected failure")
        return perform

class Transactions(unittest.TestCase):
    def test_real_invocation_unconditionally_blocked(self):
        with self.assertRaises(m.CutoverBlocked):
            m.coordinate(MockOps())
    def test_success_holds_maintenance(self):
        ops=MockOps()
        result=m.coordinate(ops,trusted_test_context=True)
        self.assertEqual(result["status"],"SINGLE_HEALTHY_MAINTENANCE_HELD")
        self.assertFalse(result["admission_reopened"])
        self.assertEqual(ops.calls,list(m.STEPS))
    def test_single_start_failure_restores_four(self):
        ops=MockOps("start_single")
        result=m.coordinate(ops,trusted_test_context=True)
        self.assertEqual(result["status"],"FOUR_RESTORED_MAINTENANCE_HELD")
        self.assertEqual(ops.calls[-5:],[
            "stop_candidate","preserve_failed_candidate_database",
            "verify_database_for_old_release","restore_four_pinned","verify_four_healthy"])
        self.assertFalse(result["admission_reopened"])
    def test_drain_failure_does_not_stop_four(self):
        ops=MockOps("attest_complete_oci_quiescence")
        with self.assertRaises(m.CutoverBlocked):
            m.coordinate(ops,trusted_test_context=True)
        self.assertNotIn("stop_four",ops.calls)
        self.assertNotIn("restore_four_pinned",ops.calls)
    def test_failed_candidate_stop_blocks_old_start(self):
        class TwoFailures(MockOps):
            def __getattr__(self,name):
                def perform():
                    self.calls.append(name)
                    if name in ("verify_single","stop_candidate"):
                        raise RuntimeError("fault")
                return perform
        ops=TwoFailures()
        with self.assertRaises(m.RecoveryFailed):
            m.coordinate(ops,trusted_test_context=True)
        self.assertNotIn("restore_four_pinned",ops.calls)
    def test_failed_state_preservation_blocks_restarting_four(self):
        class CannotPreserve(MockOps):
            def __getattr__(self,name):
                def perform():
                    self.calls.append(name)
                    if name in ("verify_single","preserve_failed_candidate_database"):
                        raise RuntimeError("fault")
                return perform
        ops=CannotPreserve()
        with self.assertRaises(m.RecoveryFailed):
            m.coordinate(ops,trusted_test_context=True)
        self.assertIn("stop_candidate",ops.calls)
        self.assertNotIn("verify_database_for_old_release",ops.calls)
        self.assertNotIn("restore_four_pinned",ops.calls)

    def test_failure_before_stop_keeps_old_stack_untouched(self):
        ops=MockOps("enter_maintenance")
        with self.assertRaises(m.CutoverBlocked):
            m.coordinate(ops,trusted_test_context=True)
        self.assertNotIn("stop_four",ops.calls)

    def test_db_incompatibility_blocks_old_start(self):
        class Incompatible(MockOps):
            def __getattr__(self,name):
                def perform():
                    self.calls.append(name)
                    if name in ("verify_single","verify_database_for_old_release"):
                        raise RuntimeError("fault")
                return perform
        ops=Incompatible()
        with self.assertRaises(m.RecoveryFailed):
            m.coordinate(ops,trusted_test_context=True)
        self.assertNotIn("restore_four_pinned",ops.calls)

if __name__=="__main__":
    unittest.main()
