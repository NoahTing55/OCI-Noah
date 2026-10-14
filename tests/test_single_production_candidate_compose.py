"""Structural safety regression for the production candidate manifest (no Docker calls)."""
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
CANDIDATE=ROOT/"deploy/compose.production-candidate.yml"

class ProductionCandidateManifest(unittest.TestCase):
    def test_real_yaml_line_breaks_and_expected_project(self):
        text=CANDIDATE.read_text()
        self.assertNotIn(r"\nname:",text)
        self.assertNotIn(r"\n#",text)
        self.assertTrue(any(line=="name: oci-nt-v2" for line in text.splitlines()))
        self.assertTrue(text.startswith("# PRODUCTION CANDIDATE"))

    def test_pinned_images_and_restricted_socket(self):
        text=CANDIDATE.read_text()
        self.assertIn("oci-noah-single@sha256:395e79d83495edd5f659b566d797ed160396a05ebb5ef098d62d853bf90da8bd",text)
        self.assertIn("oci-noah-guard@sha256:ecece550ded5e45f6db0acb3a7f71e629438de3f725d926da74618f1174ddbee",text)
        self.assertIn("OCI_NT_PROD_ENV_FILE:?",text)
        self.assertIn("OCI_NT_PROD_DATA_DIR:?",text)
        self.assertIn("OCI_NT_PROD_LOG_DIR:?",text)
        self.assertIn("network_mode: none",text)
        self.assertIn("- /var/run/docker.sock:/var/run/docker.sock:ro",text)
        self.assertIn("- guard-socket:/run/oci-guard:ro",text)

if __name__=="__main__":
    unittest.main()
