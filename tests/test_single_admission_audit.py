import importlib.util
import tempfile
import unittest
from pathlib import Path

PATH=Path(__file__).resolve().parents[1]/"deploy/single/admission-coverage-audit.py"
spec=importlib.util.spec_from_file_location("release_admission_audit",PATH)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class AdmissionCoverageTests(unittest.TestCase):
    def test_real_source_has_required_guards_but_remains_blocked(self):
        result=mod.audit()
        self.assertEqual(result["status"],"STATIC_CONTRACT_OK")
        self.assertFalse(result["production_cutover_allowed"])
        self.assertGreater(len(result["unverified_runtime_paths"]),0)
        notification = (mod.BASE/"launch_task_service.py").read_text()
        self.assertIn('kind="launch_notification"',notification)
        self.assertIn("run_with_lease(",notification)

    def test_async_spawn_inventory_exposes_new_unreviewed_sites(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            for filename in (*mod.TRACKED,"main.py","runtime_drain_snapshot.py"):
                (base/filename).write_text("asyncio.create_task(\n",encoding="utf8")
            (base/"new_background_job.py").write_text(
                "import asyncio\nasync def run():\n"
                "    asyncio.create_task(worker())\n"
                "    await asyncio.to_thread(call_oci)\n",encoding="utf8")
            result=mod.audit(base)
            found={(item["file"],item["kind"]) for item in result["spawn_inventory"]}
            self.assertIn(("new_background_job.py","asyncio_create_task"),found)
            self.assertIn(("new_background_job.py","asyncio_to_thread"),found)
            self.assertTrue(all(item["requires_manual_oci_egress_review"]
                                for item in result["spawn_inventory"]))
            self.assertFalse(result["production_cutover_allowed"])

    def test_missing_guard_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            for filename in (*mod.TRACKED,"main.py","runtime_drain_snapshot.py"):
                (base/filename).write_text("asyncio.create_task(\n",encoding="utf8")
            result=mod.audit(base)
            self.assertEqual(result["status"],"STATIC_CONTRACT_FAILED")
            self.assertFalse(result["release_authorized"])

if __name__=="__main__":
    unittest.main()
