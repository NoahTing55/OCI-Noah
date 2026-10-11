"""Release admission must count potentially live OCI HTTP reads, not writes alone."""
import importlib.util
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
p=ROOT/"backend/app/maintenance_route_guard.py"
spec=importlib.util.spec_from_file_location("maintenance_route_guard_ci",p)
guard=importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

class OCIReadAdmission(unittest.TestCase):
    def test_live_oci_gets_are_leased(self):
        for path in ("/api/v1/instances","/api/v1/instances/abc",
                     "/api/v1/accounts/42/instances","/api/v1/resources/regions",
                     "/api/v1/launch/resources/images"):
            with self.subTest(path=path):
                self.assertTrue(guard.protected_oci_request("GET",path))
    def test_local_read_and_cancel_available(self):
        for method,path in (("GET","/api/v1/health"),
                            ("GET","/api/v1/auth/me"),
                            ("GET","/api/v1/system/release/runtime-drain"),
                            ("POST","/api/v1/tasks/1/cancel")):
            with self.subTest(path=path):
                self.assertFalse(guard.protected_oci_request(method,path))
    def test_existing_writes_remain_leased(self):
        self.assertTrue(guard.protected_oci_request("POST","/api/v1/instances/sync"))
        self.assertTrue(guard.protected_oci_request("POST","/api/v1/bulk/account-checks"))
    def test_real_http_middleware_uses_shared_gate(self):
        main=(ROOT/"backend/app/main.py").read_text(encoding="utf-8")
        self.assertEqual(main.count("if protected_oci_request(request.method, request.url.path):"),2)

if __name__=="__main__":
    unittest.main()
