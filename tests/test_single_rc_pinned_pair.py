"""Validate published immutable RC pair matches review-only candidate topology."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
REVIEW=ROOT/"deploy/compose.external-guard-rc.review.yml"
SINGLE="e662ba844f851e763cb6080d177cf4ecb99f96478f92fcb154a1040824830980"
GUARD="776cac89106015ee7475690cc3cc19e163cd846816baec7db1d5fb65c32aab01"

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
