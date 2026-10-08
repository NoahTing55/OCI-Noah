import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evidence", ROOT / "deploy/backend-release-evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class EvidenceTests(unittest.TestCase):
    def test_healthy_containers(self):
        calls = []
        def fake(cmd, **kwargs):
            self.assertEqual(cmd[:2], ["docker", "inspect"])
            calls.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
                {"Running": True, "Health": {"Status": "healthy"}}
            ))
        result = evidence.container_evidence(fake)
        self.assertEqual(tuple(calls), evidence.CONTAINERS)
        self.assertTrue(all(v["health"] == "healthy" for v in result.values()))

    def test_failure_refuses_partial_result(self):
        def fail(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)
        with self.assertRaises(subprocess.CalledProcessError):
            evidence.container_evidence(fail)

    def test_collected_evidence_never_authorizes_release(self):
        original = evidence.load_drain
        class Stub:
            @staticmethod
            def report(db):
                return {"db_task_drain_ready": True}
        evidence.load_drain = lambda: Stub
        try:
            def fake(cmd, **kwargs):
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
                    {"Running": True, "Health": {"Status": "healthy"}}
                ))
            report = evidence.collect(Path("/nonexistent"), fake)
            self.assertTrue(report["evidence_complete"])
            self.assertFalse(report["release_authorized"])
            self.assertTrue(report["unverified"])
        finally:
            evidence.load_drain = original


if __name__ == "__main__":
    unittest.main()
