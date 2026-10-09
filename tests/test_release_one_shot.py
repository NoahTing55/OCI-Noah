import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class ReleaseScriptTests(unittest.TestCase):
    def test_guarded_release_contract(self):
        s=(ROOT/"deploy/release-one-shot.sh").read_text()
        for expected in ("--apply","ACCEPT OCI INTERRUPTION","sleep 120","verified-release-backup.py","backend-preflight.py","--no-deps --no-build --force-recreate","recover()","docker start oci-nt-web","BACKEND_RELEASE_OK"):
            self.assertIn(expected,s)
        self.assertNotIn("docker compose pull && docker compose up -d",s)
        self.assertNotIn("docker compose down",s)
        self.assertNotIn("docker volume rm",s)
    def test_no_unconditional_apply(self):
        s=(ROOT/"deploy/release-one-shot.sh").read_text()
        self.assertLess(s.index('[[ "$MODE" == "--apply" ]]'),s.index("docker stop --time 30 oci-nt-web"))
        self.assertLess(s.index("read -r -p"),s.index("docker stop --time 30 oci-nt-web"))
        self.assertIn('[[ "$ACK" == "ACCEPT OCI INTERRUPTION" ]]',s)
if __name__=="__main__":unittest.main()
