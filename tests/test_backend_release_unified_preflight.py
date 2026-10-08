import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("release_preflight", Path(__file__).resolve().parents[1] / "deploy/backend-release-preflight.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class UnifiedPreflightTests(unittest.TestCase):
    def test_all_checks_pass_still_no_authorization(self):
        calls = []
        def fake(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"ok": True}))
        report = module.assess(Path("/unused.db"), "a"*40, fake)
        self.assertEqual(len(calls), 4)
        self.assertTrue(report["observed_checks_passed"])
        self.assertFalse(report["release_authorized"])
        self.assertEqual(report["status"], "EVIDENCE_ONLY")
        self.assertFalse(any("apply" in item for cmd in calls for item in cmd))

    def test_failed_check_blocks(self):
        def fake(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 3, stdout='{"drain":"BLOCKED"}')
        report = module.assess(Path("/unused.db"), "a"*40, fake)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["release_authorized"])

    def test_broken_json_blocks(self):
        def fake(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="broken json")
        self.assertFalse(module.assess(Path("/unused.db"), "a"*40, fake)["observed_checks_passed"])

    def test_missing_checker_blocks(self):
        def fake(cmd, **kwargs):
            raise FileNotFoundError("missing")
        report = module.assess(Path("/unused.db"), "a"*40, fake)
        self.assertFalse(report["observed_checks_passed"])


if __name__ == "__main__":
    unittest.main()
