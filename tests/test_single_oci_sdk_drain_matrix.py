"""OCI entrypoint drain coverage regression against the actual dispatch graph.

This tests candidate source safety boundaries, NOT running production quiescence.
"""
import ast
from pathlib import Path
import unittest

APP = Path(__file__).resolve().parents[1] / "backend" / "app"

def method(name, function):
    tree=ast.parse((APP/name).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==function)

def calls(fn):
    return {ast.unparse(n.func).split(".")[-1]
            for n in ast.walk(fn) if isinstance(n,ast.Call)}

class SDKDrainMatrixTests(unittest.TestCase):
    def test_live_metric_threadpool_lies_behind_protected_get(self):
        self.assertIn("ThreadPoolExecutor",calls(method("oci_rc_service.py","query_instance_metrics")))
        self.assertIn("query_instance_metrics",calls(method("resource_router.py","metrics_route")))
        from app.maintenance_route_guard import protected_oci_request
        self.assertTrue(protected_oci_request("GET","/api/v1/resources/accounts/1/instances/x/metrics"))
        self.assertTrue(protected_oci_request("GET","/api/v1/tasks/1/safe-resume-preview"))

    def test_launch_worker_is_leased(self):
        fn=method("launch_task_service.py","start_launch_task")
        self.assertIn("run_with_lease",calls(fn))
        self.assertIn("adaptive_launch_call",calls(method("launch_task_service.py","_run_launch_task")))

    def test_bulk_worker_enrolled_in_process_shutdown_drain(self):
        fn=method("oci_background_leases.py","await_registered_workers")
        code=ast.unparse(fn)
        self.assertIn("bulk_check_service._tasks.values()",code)
        self.assertIn("_detached_workers",code)
        wrapper=method("bulk_check_service.py","_run_bulk_check_with_lease")
        self.assertIn("run_with_lease",calls(wrapper))
        self.assertIn("finish_job",calls(wrapper))

    def test_no_process_scoped_report_claims_global_quiescence(self):
        from app.oci_http_inflight import snapshot
        self.assertEqual(snapshot()["process_scope"],"current_api_process_only")
        self.assertFalse(snapshot()["release_authorized"])

if __name__=="__main__":
    unittest.main()
