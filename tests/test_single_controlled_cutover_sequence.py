import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
path=ROOT/"deploy/single/controlled-cutover-sequence.py"
spec=importlib.util.spec_from_file_location("controlled_sequence",path)
sequence=importlib.util.module_from_spec(spec)
spec.loader.exec_module(sequence)

class ControlledSequenceTests(unittest.TestCase):
    def test_sequence_prevents_start_before_old_process_exit(self):
        report=sequence.plan()
        steps=[step["action"] for step in report["steps"]]
        def before(first,second):
            self.assertLess(steps.index(first),steps.index(second))
        before("operator_approves_window","freeze_external_ingress_and_new_oci_requests")
        before("freeze_external_ingress_and_new_oci_requests","wait_for_old_requests_and_workers_to_finish")
        before("wait_for_old_requests_and_workers_to_finish","stop_old_api_and_monitor_then_verify_process_exit")
        before("stop_old_api_and_monitor_then_verify_process_exit","verify_old_generation_has_no_remaining_sdk_executors")
        before("verify_old_generation_has_no_remaining_sdk_executors","capture_verified_cutover_point_snapshot")
        before("capture_verified_cutover_point_snapshot","start_isolated_guard_and_candidate_using_approved_data")
        before("start_isolated_guard_and_candidate_using_approved_data","verify_candidate_health_and_credential_compatibility")
        self.assertIn("if_failed_stop_candidate_and_restore_legacy_four_services",steps)
        self.assertFalse(report["cutover_authorized"])
        self.assertFalse(report["sdk_global_quiescence_proven"])
        self.assertFalse(report["can_run_on_production"])
        self.assertFalse(report["production_changes_performed"])

if __name__=="__main__":
    unittest.main()
