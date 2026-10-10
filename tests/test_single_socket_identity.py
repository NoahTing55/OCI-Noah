"""Tests for experimental split process identities. Not a full socket threat model."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SocketIdentityTests(unittest.TestCase):
    def test_runtime_identity_separation(self):
        src=(ROOT/"deploy/single/supervise.py").read_text()
        ast.parse(src)
        self.assertIn('getpwnam("guard" if socket_access else "app")', src)
        self.assertIn('app_identity(socket_access=True)',src)
        self.assertIn('os.setgroups([])',src)
        self.assertIn('os.setgroups([gid])',src)
        self.assertIn('gid <= 0',src)
    def test_dedicated_guard_user_exists(self):
        docker=(ROOT/"deploy/single/Dockerfile").read_text()
        self.assertIn('useradd --uid 10002',docker)
        self.assertIn('useradd --uid 10001',docker)
    def test_explicit_group_and_no_prod_compose_changes(self):
        c=(ROOT/"deploy/compose.single.yml").read_text()
        self.assertIn('DOCKER_GID:',c)
        self.assertIn('docker.sock:ro',c)
        self.assertIn('oci-nt-single-lab',c)
        self.assertIn('oci-nt-docker-guard', (ROOT/"docker-compose.yml").read_text())
    def test_ci_exercises_dedicated_uid(self):
        smoke=(ROOT/"deploy/single/ci-smoke.sh").read_text()
        self.assertIn('DOCKER_GID=',smoke)
        self.assertIn('10002 if n=="app.docker_socket_guard"',smoke)
if __name__=="__main__":
    unittest.main()
