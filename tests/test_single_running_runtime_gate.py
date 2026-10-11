import importlib.util
import pathlib
import unittest

P=pathlib.Path(__file__).resolve().parents[1]/"deploy/single/running-runtime-gate.py"
spec=importlib.util.spec_from_file_location("running_runtime_gate",P)
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

class RuntimeGenerationGate(unittest.TestCase):
    def test_healthy_empty_leases_never_mean_safe_cutover(self):
        original={name:f"ghcr.io/noahting55/oci-noah-{kind}:sha-"+("a"*40)
                  for name,kind in gate.EXPECTED.items()}
        result=gate.inspect_runtime({"mode":"PLAN_ONLY","release_authorized":False,
                                    "original_images":original,"recorded_work_drained":True})
        self.assertEqual(result["status"],"BLOCKED_RUNNING_OCI_ATTESTATION")
        self.assertFalse(result["running_generation_attested"])
        self.assertFalse(result["production_cutover_allowed"])
        self.assertEqual(len(result["running_oci_services_needing_proof"]),3)
    def test_unknown_image_rejected(self):
        images={name:f"ghcr.io/noahting55/oci-noah-{kind}:sha-"+("a"*40)
                for name,kind in gate.EXPECTED.items()}
        images["oci-nt-api"]="docker.io/unknown:latest"
        with self.assertRaises(ValueError):
            gate.inspect_runtime({"mode":"PLAN_ONLY","release_authorized":False,
                                  "original_images":images})
    def test_apply_always_refused(self):
        import subprocess
        result=subprocess.run(["python3",str(P),"--sha","a"*40,"--apply"],text=True,capture_output=True)
        self.assertEqual(result.returncode,4)
        self.assertIn('"release_authorized": false',result.stdout)

if __name__=="__main__": unittest.main()
