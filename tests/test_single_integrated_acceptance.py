from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"deploy/single/ci-four-single-four.sh"

class IntegratedSwitchContract(unittest.TestCase):
    def test_ci_only_and_no_existing_database(self):
        s=SCRIPT.read_text()
        self.assertIn('RUNNER_ENVIRONMENT:-',s)
        self.assertIn('GITHUB_ACTIONS:-',s)
        self.assertIn('REFUSE_EXISTING_DATABASE',s)
        self.assertIn('trap cleanup EXIT',s)
    def test_real_four_single_four_switch(self):
        s=SCRIPT.read_text()
        self.assertEqual(s.count('docker compose -p oci-nt -f docker-compose.yml up -d --no-build'),3)
        self.assertIn('docker compose -p oci-nt -f docker-compose.yml down',s)
        self.assertIn('docker run -d --name oci-nt-single-ci-switch',s)
        self.assertIn('docker stop --time 35 oci-nt-single-ci-switch',s)
        for marker in ("FOUR_CONTAINER_BASELINE_OK","FOUR_TO_SINGLE_OK","SINGLE_TO_FOUR_ROLLBACK_OK","FAILED_SINGLE_TO_FOUR_RECOVERY_OK","PRAGMA quick_check", "ci_switch_marker"):
            self.assertIn(marker,s)
    def test_no_real_credentials_or_oci_actions(self):
        s=SCRIPT.read_text()
        self.assertNotIn('/root/oci-nt-backups',s)
        self.assertNotIn('oci launch',s)
        self.assertIn('ci-only-not-a-secret',s)
        self.assertIn('DOCKER_GUARD_HOST=127.0.0.1',s)

if __name__=="__main__":
    unittest.main()
