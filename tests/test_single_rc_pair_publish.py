"""RC paired GHCR publish requires opt-in and never deploys to production."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
class PairPublish(unittest.TestCase):
    def test_dispatch_is_explicit_and_branch_scoped(self):
        src=(ROOT/".github/workflows/ci-ghcr.yml").read_text()
        block=src.split("\n  publish_single_rc:",1)[1]
        for required in ("github.event_name == 'workflow_dispatch'",
                         "github.ref == 'refs/heads/release/2.0-single-container-rc'",
                         "inputs.publish_single_rc == true",
                         "needs: test","packages: write"):
            self.assertIn(required,block)
        for required in ("id: build","id: guard_build",
                         "./deploy/single/Dockerfile.guard",
                         "ghcr.io/noahting55/oci-noah-guard:sha-",
                         "ghcr.io/noahting55/oci-noah-single:sha-",
                         "GUARD_DIGEST:","RC_DIGEST:"):
            self.assertIn(required,block)
        self.assertEqual(block.count("push: true"),2)
        self.assertNotIn("docker compose up",block)
        self.assertNotIn("docker pull ",block)
        self.assertNotIn("--apply",block)

if __name__=="__main__":
    unittest.main()
