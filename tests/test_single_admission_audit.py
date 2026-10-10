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
