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
        self.assertIn('-e DOCKER_GUARD_HOST=127.0.0.1',smoke)
        self.assertIn('-e DOCKER_GUARD_PORT=9861',smoke)
        self.assertIn('10002 if n=="app.docker_socket_guard"',smoke)
    def test_docker_guard_rejects_mutating_and_ambiguous_paths(self):
        # All tests are offline: no Docker socket requests are sent.
        import sys
        sys.path.insert(0, str(ROOT/"backend"))
        from app.docker_socket_guard import allowed_target, Handler
        accepted = (
            "/version", "/containers/json?all=1",
            "/containers/" + "a"*12 + "/json",
            "/containers/" + "a"*64 + "/stats?stream=false",
        )
        rejected = (
            "/containers/create", "/containers/prune", "/images/json",
            "/events", "/exec/123/start", "/containers/json",
            "/containers/json?all=0", "/containers/json?all=1&size=1",
            "/containers/" + "a"*12 + "/restart",
            "/containers/" + "a"*12 + "/kill",
            "/containers/" + "a"*12 + "/stats?stream=true",
            "/containers/%61" + "a"*11 + "/json",
            "http://127.0.0.1/version", "//evil.example/version",
            "/version#fragment", "/version\\\\evil", "/version?bad=1",
            "/"+"x"*300,
        )
        for target in accepted:
            with self.subTest(accepted=target):
                self.assertTrue(allowed_target(target))
        for target in rejected:
            with self.subTest(rejected=target):
                self.assertFalse(allowed_target(target))
        for method in ("do_POST","do_PUT","do_PATCH","do_DELETE"):
            self.assertIs(getattr(Handler,method), Handler.do_POST)

if __name__=="__main__":
    unittest.main()
