"""Regression coverage for real OCI-facing FastAPI routes.

This verifies route declarations against the production admission classifier,
not merely the presence of a lease helper somewhere in the repository.
It does NOT attest live OCI SDK threads or authorize a cutover.
"""
import ast
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
MAIN=ROOT/"backend/app/main.py"
GUARD=ROOT/"backend/app/maintenance_route_guard.py"
spec=importlib.util.spec_from_file_location("oci_guard_contract",GUARD)
guard=importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

def declared_routes():
    tree=ast.parse(MAIN.read_text())
    found={}
    for node in tree.body:
        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec,ast.Call) or not isinstance(dec.func,ast.Attribute):
                continue
            if dec.func.attr not in ("get","post","put","patch","delete"):
                continue
            if not isinstance(dec.func.value,ast.Name) or dec.func.value.id!="app":
                continue
            if not dec.args or not isinstance(dec.args[0],ast.JoinedStr):
                continue
            parts=dec.args[0].values
            pattern="".join(x.value if isinstance(x,ast.Constant) and isinstance(x.value,str) else "/api/v1" if isinstance(x,ast.FormattedValue) else "" for x in parts)
            found[(dec.func.attr.upper(),pattern)]=node.name
    return found

# Only known OCI request entry points; tests fail if declarations move or
# maintenance classification changes. Must expand on adding new OCI routes.
MUST_LEASE={
    ("POST","/api/v1/accounts/import-preview"),
    ("POST","/api/v1/accounts"),
    ("POST","/api/v1/accounts/{account_id}/check"),
    ("POST","/api/v1/bulk/account-checks"),
    ("POST","/api/v1/tasks/instance-batch"),
    ("POST","/api/v1/instances/sync"),
    ("POST","/api/v1/accounts/{account_id}/instances/sync"),
    ("POST","/api/v1/accounts/{account_id}/instances/{instance_id}/actions"),
    ("GET","/api/v1/accounts/{account_id}/instances"),
    ("GET","/api/v1/instances"),
}

class OCIEntrypointCoverage(unittest.TestCase):
    def test_declared_oci_routes_have_durable_http_admission(self):
        found=declared_routes()
        for method,path in sorted(MUST_LEASE):
            with self.subTest(method=method,path=path):
                self.assertIn((method,path),found)
                self.assertTrue(guard.protected_oci_request(method,path),
                                "OCI request route not admitted through durable lease")
    def test_account_root_creation_is_oci_leased(self):
        self.assertTrue(guard.protected_oci_write("POST","/api/v1/accounts"))
        self.assertTrue(guard.protected_oci_request("POST","/api/v1/accounts"))
        self.assertFalse(guard.protected_oci_request("GET","/api/v1/accounts"))

    def test_local_only_routes_do_not_grab_oci_lease(self):
        for method,path in (("GET","/api/v1/auth/me"),("GET","/api/v1/system/release"),
                            ("GET","/api/v1/health")):
            self.assertFalse(guard.protected_oci_request(method,path))
    def test_maintenance_drain_remains_explicitly_unproven(self):
        source=(ROOT/"deploy/single/running-runtime-gate.py").read_text()
        self.assertIn('"running_generation_attested": False',source)
        self.assertIn('"production_cutover_allowed": False',source)

if __name__=="__main__":
    unittest.main()
