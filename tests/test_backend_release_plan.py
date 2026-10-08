import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("plan", Path(__file__).resolve().parents[1] / "deploy/backend-release-plan.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class BackendPlanTests(unittest.TestCase):
    def test_good_evidence_never_approves_release(self):
        commands=[]
        def runner(cmd, **kw):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"status":"ok"}))
        result=module.plan(Path("/tmp/example.db"),"a"*40,runner)
        self.assertEqual(len(commands),4)
        self.assertTrue(result["observed_checks_passed"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["execution_supported"])
        self.assertTrue(result["unimplemented_blockers"])
        self.assertTrue(all(c[0].endswith("python") or "python" in c[0] for c in commands))

    def test_failed_checker_keeps_plan_blocked(self):
        def runner(cmd, **kw):
            return subprocess.CompletedProcess(cmd,3,stdout='{"status":"BLOCKED"}')
        result=module.plan(Path("/tmp/example.db"),"a"*40,runner)
        self.assertFalse(result["observed_checks_passed"])
        self.assertFalse(result["release_authorized"])

    def test_reject_invalid_revision(self):
        with self.assertRaises(ValueError):
            module.plan(Path("/tmp/example.db"),"latest")

if __name__=="__main__":
    unittest.main()
