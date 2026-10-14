import importlib.util
from pathlib import Path
import unittest

src=Path(__file__).resolve().parents[1]/"deploy/single/final-rollback-admission.py"
spec=importlib.util.spec_from_file_location("rollback_admission",src)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def complete():
    evidence={key:True for key in m.CHECKS}
    evidence["original_image_ids"]={key:"sha256:"+str(i)*64 for i,key in enumerate(m.OLD)}
    evidence["final_snapshot_sha256"]="a"*64
    evidence["candidate_stopped_for_rollback"]=True
    return evidence

class RollbackAdmission(unittest.TestCase):
    def test_complete_still_never_authorizes(self):
        result=m.inspect(complete())
        self.assertEqual(result["status"],"ROLLBACK_EVIDENCE_COMPLETE_PLAN_ONLY")
        self.assertFalse(result["production_cutover_authorized"])
        self.assertFalse(result["sdk_global_quiescence_proven"])
        self.assertFalse(result["live_restore_verified"])
    def test_each_required_property_blocks(self):
        for key in m.CHECKS:
            with self.subTest(key=key):
                e=complete();e[key]=False
                self.assertEqual(m.inspect(e)["status"],"BLOCKED_ROLLBACK_EVIDENCE")
    def test_candidate_not_stopped_blocks(self):
        e=complete();e["candidate_stopped_for_rollback"]=False
        self.assertIn("candidate_stopped_for_rollback",m.inspect(e)["missing"])
    def test_image_inventory_and_snapshot_digest_fail_closed(self):
        e=complete();e["original_image_ids"].pop("oci-nt-web")
        with self.assertRaises(ValueError):m.inspect(e)
        e=complete();e["final_snapshot_sha256"]="bad"
        self.assertEqual(m.inspect(e)["status"],"BLOCKED_ROLLBACK_EVIDENCE")
if __name__=="__main__":unittest.main()
