"""Fail-closed source contract: existing admission is NOT a global drain proof."""
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/"backend/app"

class GlobalDrainCoverageContract(unittest.TestCase):
    def test_http_request_lease_surrounds_downstream_request(self):
        src=(APP/"main.py").read_text()
        tree=ast.parse(src)
        found=False
        for node in ast.walk(tree):
            if not isinstance(node,ast.AsyncFunctionDef) or node.name!="maintenance_request_guard":
                continue
            s=ast.get_source_segment(src,node) or ""
            self.assertIn("protected_oci_request(",s)
            self.assertIn("acquire_oci_lease(",s)
            self.assertIn("release_oci_lease(",s)
            self.assertIn("call_next(request)",s)
            found=True
        self.assertTrue(found)

    def test_lease_admission_serializes_maintenance_with_sqlite(self):
        s=(APP/"oci_operation_leases.py").read_text()
        self.assertIn('con.execute("BEGIN IMMEDIATE")',s)
        self.assertIn('marker.get("active") is True',s)
        self.assertIn("validate_lease_records(",s)
        self.assertIn('con.execute("COMMIT")',s)

    def test_cancelled_background_work_retains_lease(self):
        s=(APP/"oci_background_leases.py").read_text()
        self.assertIn("asyncio.shield(worker)",s)
        self.assertIn("if done.cancelled():",s)
        self.assertIn("durable lease retained",s)
        self.assertIn('"production_quiescence_attested": False',s)
        self.assertIn('"scope": "current_api_process_only"',s)

    def test_route_guard_does_not_claim_complete_sdk_interception(self):
        s=(APP/"maintenance_route_guard.py").read_text()
        self.assertIn("not an atomic task admission",s)
        self.assertIn("def protected_oci_request(",s)
        self.assertIn('method.upper() != "GET"',s)
        self.assertIn('subpath.startswith(("resources/", "launch/resources/", "oci/"))',s)

    def test_runtime_gate_never_promotes_absent_leases_to_global_drain(self):
        s=(ROOT/"deploy/single/running-runtime-gate.py").read_text()
        for token in ('"status": "BLOCKED_RUNNING_OCI_ATTESTATION"',
                      '"leases_are_global_quiescence_proof": False',
                      '"running_generation_attested": False',
                      '"production_cutover_allowed": False'):
            self.assertIn(token,s)

if __name__=="__main__":
    unittest.main()
