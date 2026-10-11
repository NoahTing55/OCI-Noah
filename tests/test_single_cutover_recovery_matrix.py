"""Cutover transaction fail-closed recovery state-machine regression matrix."""
import importlib.util
import pathlib
import unittest

script=pathlib.Path(__file__).resolve().parents[1]/"deploy/single/cutover-transaction.py"
spec=importlib.util.spec_from_file_location("cutover_transaction_matrix",script)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

RECOVERY=("stop_candidate","preserve_failed_candidate_database",
          "verify_database_for_old_release","restore_four_pinned","verify_four_healthy")

class Ops:
    def __init__(self, fail_at):
        self.fail_at=fail_at
        self.calls=[]
    def __getattr__(self,name):
        if name not in mod.STEPS+RECOVERY:
            raise AttributeError(name)
        def action():
            self.calls.append(name)
            if name==self.fail_at:
                raise RuntimeError("injected synthetic fault")
        return action

class CutoverMatrix(unittest.TestCase):
    def test_no_production_adapter_without_explicit_test_context(self):
        ops=Ops(None)
        with self.assertRaises(mod.CutoverBlocked):
            mod.coordinate(ops)
        self.assertEqual(ops.calls,[])

    def test_success_always_keeps_maintenance_held(self):
        ops=Ops(None)
        result=mod.coordinate(ops,trusted_test_context=True)
        self.assertEqual(result["status"],"SINGLE_HEALTHY_MAINTENANCE_HELD")
        self.assertFalse(result["admission_reopened"])
        self.assertEqual(ops.calls,list(mod.STEPS))

    def test_every_pre_stop_fault_is_blocked_without_mutating_container(self):
        for step in mod.STEPS[:5]:
            with self.subTest(step=step):
                ops=Ops(step)
                with self.assertRaises(mod.CutoverBlocked):
                    mod.coordinate(ops,trusted_test_context=True)
                self.assertNotIn("stop_four",ops.calls)
                self.assertNotIn("restore_four_pinned",ops.calls)

    def test_every_post_stop_fault_triggers_ordered_recovery(self):
        for step in mod.STEPS[5:]:
            with self.subTest(step=step):
                ops=Ops(step)
                result=mod.coordinate(ops,trusted_test_context=True)
                self.assertEqual(result["status"],"FOUR_RESTORED_MAINTENANCE_HELD")
                self.assertFalse(result["admission_reopened"])
                expected=(["stop_candidate"] if mod.STEPS.index(step)>=mod.STEPS.index("start_single") else [])+list(RECOVERY[1:])
                self.assertEqual(ops.calls[-len(expected):],expected)

    def test_failure_at_each_recovery_stage_never_advances(self):
        for action in RECOVERY:
            with self.subTest(action=action):
                ops=Ops(action)
                # First cause a candidate-health fault, then fail one rollback action.
                def injected(name):
                    def run():
                        ops.calls.append(name)
                        if name=="verify_single" or name==action:
                            raise RuntimeError("synthetic failure")
                    return run
                ops.__getattr__=None
                class RollbackFault:
                    calls=ops.calls
                    def __getattr__(self,name):
                        if name not in mod.STEPS+RECOVERY: raise AttributeError(name)
                        return injected(name)
                obj=RollbackFault()
                with self.assertRaises(mod.RecoveryFailed):
                    mod.coordinate(obj,trusted_test_context=True)
                position=obj.calls.index(action)
                self.assertNotIn("verify_four_healthy",obj.calls[position+1:])
                if action in ("stop_candidate","preserve_failed_candidate_database","verify_database_for_old_release"):
                    self.assertNotIn("restore_four_pinned",obj.calls)

if __name__=="__main__": unittest.main()
