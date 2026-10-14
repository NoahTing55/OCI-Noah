import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/"backend/app"

class OCIEntrypointDrainCoverage(unittest.TestCase):
    def test_bulk_worker_has_durable_lease(self):
        tree=ast.parse((ROOT/"bulk_check_service.py").read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="_run_bulk_check_with_lease")
        calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call)]
        lease=[n for n in calls if isinstance(n.func,ast.Name) and n.func.id=="run_with_lease"]
        self.assertEqual(len(lease),1)
        kwargs={kw.arg:kw.value for kw in lease[0].keywords}
        self.assertEqual(ast.literal_eval(kwargs["kind"]),"bulk-check")
        self.assertIsInstance(kwargs["coroutine"],ast.Call)
        self.assertEqual(kwargs["coroutine"].func.id,"_run_bulk_check")
        self.assertIn("oci_background_leases", (ROOT/"bulk_check_service.py").read_text())

    def test_get_oci_preview_and_metrics_gated(self):
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from app.maintenance_route_guard import protected_oci_request
        self.assertTrue(protected_oci_request("GET","/api/v1/tasks/12/safe-resume-preview"))
        self.assertTrue(protected_oci_request("GET","/api/v1/resources/accounts/5/instances/i/metrics"))
        self.assertFalse(protected_oci_request("GET","/api/v1/health"))
        self.assertFalse(protected_oci_request("GET","/api/v1/auth/me"))

if __name__=="__main__":unittest.main()
