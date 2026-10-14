"""Validate published immutable RC pair matches review-only candidate topology."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
REVIEW=ROOT/"deploy/compose.external-guard-rc.review.yml"
SINGLE="395e79d83495edd5f659b566d797ed160396a05ebb5ef098d62d853bf90da8bd"
GUARD="ecece550ded5e45f6db0acb3a7f71e629438de3f725d926da74618f1174ddbee"

class PublishedPair(unittest.TestCase):
    def test_exact_pinned_digests_and_isolation(self):
        s=REVIEW.read_text()
        self.assertIn("name: oci-nt-external-guard-rc-review",s)
        self.assertIn("oci-noah-single@sha256:"+SINGLE,s)
        self.assertIn("oci-noah-guard@sha256:"+GUARD,s)
        self.assertNotIn("oci-nt-single:external-guard-lab",s)
        self.assertNotIn("build:",s)
        for marker in ("OCI_NT_RC_ENV_FILE:?", "OCI_NT_RC_DATA_DIR:?", "OCI_NT_RC_LOG_DIR:?","guard-socket:/run/oci-guard:ro"):
            self.assertIn(marker,s)
        self.assertNotIn("../data:/app/data",s)
        self.assertNotIn("../logs:/app/logs",s)
        self.assertEqual(s.count("restart: unless-stopped"),2)

if __name__=="__main__":
    unittest.main()
