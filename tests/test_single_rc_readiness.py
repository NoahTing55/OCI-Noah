"""RC gate rejects incomplete, fabricated or unauthorized cutover evidence."""
import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).resolve().parents[1]/"deploy/single/rc-readiness.py"
spec=importlib.util.spec_from_file_location("rc_readiness",p)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def good():
    return {"manifest_version":1,"scope":"/opt/oci-nt",
            "checks":{key:True for key in mod.REQUIRED}}

class Readiness(unittest.TestCase):
    def test_complete_report_never_authorizes_cutover(self):
        result=mod.assess(good())
        self.assertFalse(result["cutover_apply_allowed"])
        self.assertFalse(result["single_container_production_authorized"])
        self.assertFalse(result["production_executor_integrated"])
    def test_all_true_still_discloses_unverified_live_evidence(self):
        result=mod.assess(good())
        self.assertEqual(result["evidence_trust"],"self_reported_unverified")
        self.assertFalse(result["ci_success_is_production_authorization"])
        self.assertIn("live_oci_leases_drained",
                      result["live_or_operator_proofs_not_verified_by_ci"])
        self.assertIn("docker_socket_threat_review_approved",
                      result["live_or_operator_proofs_not_verified_by_ci"])
        self.assertFalse(result["cutover_apply_allowed"])

    def test_every_missing_proof_blocks(self):
        for key in mod.REQUIRED:
            with self.subTest(proof=key):
                record=good()
                record["checks"][key]=False
                result=mod.assess(record)
                self.assertIn(key,result["missing_proofs"])
                self.assertFalse(result["cutover_apply_allowed"])
    def test_reject_unknown_fields_types_or_scope(self):
        samples=[]
        r=good();r["scope"]="/opt/oci--nt";samples.append(r)
        r=good();r["scope"]="/opt/oci-nt-experimental";samples.append(r)
        r=good();r["checks"].pop("detached_tasks_drained");samples.append(r)
        r=good();r["checks"]["unexpected"]=True;samples.append(r)
        r=good();r["checks"]["stable_ui_parity"]=1;samples.append(r)
        r=good();r["manifest_version"]=True;samples.append(r)
        r=good();r["extra"]="ignored";samples.append(r)
        for index,record in enumerate(samples):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):mod.assess(record)
    def test_required_scopes_and_safety_proofs(self):
        self.assertIn("api_routes_parity",mod.REQUIRED)
        self.assertIn("credentials_restored_in_isolated_test",mod.REQUIRED)
        self.assertIn("all_writers_gate_enforced",mod.REQUIRED)
        self.assertIn("docker_socket_threat_review_approved",mod.REQUIRED)

if __name__=="__main__":
    unittest.main()
