import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/"deploy/single/stopped-generation-evidence.py"
spec=importlib.util.spec_from_file_location("stopped_evidence",path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def sample():
    return {"ingress_frozen":True,"containers":{
        name:{"running":False,"state":"exited","pid":0} for name in mod.REQUIRED
    }}

class StopEvidenceTests(unittest.TestCase):
    def test_all_exited_is_only_plan_evidence(self):
        result=mod.evaluate(sample())
        self.assertEqual(result["status"],"EXIT_EVIDENCE_COMPLETE_PLAN_ONLY")
        self.assertTrue(result["old_generation_process_exit_evidenced"])
        self.assertFalse(result["inflight_sdk_calls_were_drained_before_stop"])
        self.assertFalse(result["global_external_requests_absent_proven"])
        self.assertFalse(result["production_cutover_authorized"])
    def test_unfrozen_ingress_blocks(self):
        x=sample();x["ingress_frozen"]=False
        self.assertEqual(mod.evaluate(x)["status"],"BLOCKED_EXIT_EVIDENCE")
    def test_running_or_pid_mismatch_blocks(self):
        for change in ({"running":True},{"state":"running"},{"pid":22},{"pid":False}):
            x=sample();x["containers"]["oci-nt-api"].update(change)
            self.assertEqual(mod.evaluate(x)["status"],"BLOCKED_EXIT_EVIDENCE")
    def test_missing_guard_and_bad_input_fail_closed(self):
        x=sample();del x["containers"]["oci-nt-docker-guard"]
        with self.assertRaises(ValueError):mod.evaluate(x)
        with self.assertRaises(ValueError):mod.evaluate({})
if __name__=="__main__":
    unittest.main()
