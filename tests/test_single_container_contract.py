"""Source and isolation guards for experimental single-container packaging."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class SingleContainerContract(unittest.TestCase):
    def test_not_altering_four_container_production(self):
        compose=(ROOT/"docker-compose.yml").read_text()
        self.assertIn("oci-nt-api",compose)
        self.assertIn("oci-nt-web",compose)
        candidate=(ROOT/"deploy/compose.single.yml").read_text()
        self.assertIn("name: oci-nt-single-lab",candidate)
        self.assertNotIn("oci-nt-api\n",candidate)
        self.assertIn("stop_grace_period: 35s",candidate)
        self.assertIn("docker.sock:ro",candidate)
    def test_supervised_lifecycle(self):
        script=(ROOT/"deploy/single/supervise.py").read_text()
        for name in ("docker-guard","monitor","api","web"):
            self.assertIn(name,script)
        for marker in ("SIGTERM","start_new_session=True","os.killpg","proc.poll()","os.setuid"):
            self.assertIn(marker,script)
    def test_all_four_health_checks(self):
        health=(ROOT/"deploy/single/health.py").read_text()
        for port in ("9858","9859","9860","9861"):
            self.assertIn(port,health)
    def test_build_context(self):
        dockerfile=(ROOT/"deploy/single/Dockerfile").read_text()
        for name in ("backend/requirements.txt","frontend/","nginx", "supervise.py","sites-enabled/default"):
            self.assertIn(name,dockerfile)

if __name__=="__main__": unittest.main()
