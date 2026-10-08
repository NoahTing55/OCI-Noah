import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("candidate", Path(__file__).resolve().parents[1] / "deploy/backend-candidate-check.py")
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
SHA = "a"*40

class CandidateTests(unittest.TestCase):
    def test_no_mutating_docker_commands(self):
        calls = []
        def fake(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
                "Id":"sha256:" + "b"*64,
                "RepoTags":[candidate.IMAGE+SHA],
                "Config":{"Labels":{"org.opencontainers.image.revision":SHA}},
                "Os":"linux", "Architecture":"amd64"
            }))
        result = candidate.inspect_candidate(SHA, fake)
        self.assertEqual(calls[0][:3], ["docker","image","inspect"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["candidate_compatibility_verified"])
        self.assertTrue(result["revision_label_verified"])

    def test_bad_sha_rejected_before_docker(self):
        with self.assertRaises(ValueError):
            candidate.inspect_candidate("latest", lambda *a,**k: self.fail("runner called"))

    def test_wrong_tag_rejected(self):
        def fake(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
                "Id":"sha256:"+"b"*64,"RepoTags":["other:latest"]
            }))
        with self.assertRaises(RuntimeError):
            candidate.inspect_candidate(SHA, fake)

    def test_missing_revision_label_is_not_verified(self):
        def fake(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
                "Id":"sha256:"+"b"*64,"RepoTags":[candidate.IMAGE+SHA]
            }))
        self.assertFalse(candidate.inspect_candidate(SHA, fake)["revision_label_verified"])

if __name__ == "__main__":
    unittest.main()
