import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("readiness",ROOT/"deploy/single/cutover-readiness-report.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class CutoverReadiness(unittest.TestCase):
    def test_even_fully_healthy_idle_observation_never_authorizes(self):
        audit={
            "four_containers_healthy":True,
            "database":{"schema":7,"accounts":26,"recorded_http_leases":0,
                        "manual_tasks_nonterminal":0,"launch_jobs_nonterminal":0,
                        "scheduler_enabled":False},
            "backup":{"sha256_match":True,"account_count_matches_live":True},
            "containers":[{"image_id":f"sha256:{n}"} for n in range(4)]
        }
        result=module.assess(audit)
        self.assertEqual(result["status"],"BLOCKED_PLAN_ONLY")
        self.assertFalse(result["global_oci_quiescence_proven"])
        self.assertFalse(result["production_cutover_authorized"])
        self.assertTrue(result["observations"]["no_recorded_leases"])
        self.assertTrue(result["blockers"])

    def test_missing_audit_refused(self):
        for invalid in ({},[],None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    module.assess(invalid)

if __name__=="__main__":
    unittest.main()
