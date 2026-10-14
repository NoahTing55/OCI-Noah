"""Fail-closed contracts for disposable out-of-container Unix Docker guard."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ExternalGuardContract(unittest.TestCase):
    def test_workload_has_no_raw_docker_socket_or_docker_group(self):
        src = (ROOT/"deploy/compose.external-guard-lab.yml").read_text()
        workload = src.split("  all-in-one:", 1)[1].split("\nvolumes:", 1)[0]
        guard = src.split("  docker-guard:", 1)[1].split("  all-in-one:", 1)[0]
        self.assertIn("OCI_NT_EXTERNAL_GUARD:", workload)
        self.assertIn("guard-socket:/run/oci-guard:ro", workload)
        self.assertNotIn("/var/run/docker.sock", workload)
        self.assertNotIn("group_add:", workload)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock:ro", guard)
        self.assertIn("network_mode: none", guard)
        self.assertIn("guard-socket-init:", src)
        initializer = src.split("  guard-socket-init:",1)[1].split("  docker-guard:",1)[0]
        self.assertIn("- CHOWN", initializer)
        self.assertIn("- FOWNER", initializer)
        self.assertIn("restart: \"no\"", initializer)
        self.assertIn("condition: service_completed_successfully", guard)
        self.assertIn("cap_add:", workload)
        self.assertIn("- SETUID", workload)
        self.assertIn("- SETGID", workload)
        self.assertIn("- KILL", workload)
        self.assertIn("cap_drop:", guard)
        self.assertIn("no-new-privileges:true", guard)
        self.assertIn("restart: \"no\"", guard)
        self.assertIn("name: oci-nt-external-guard-lab", src)
        self.assertNotIn("name: oci-nt\n", src)

    def test_real_docker_smoke_uses_monitor_identity_for_socket(self):
        smoke=(ROOT/"deploy/single/ci-external-guard.sh").read_text()
        self.assertIn('docker exec -i --user 10001:10001 "$workload" python', smoke)
        self.assertNotIn('docker exec -i "$workload" python', smoke)
        self.assertIn('docker exec --user 10001:10001 "$workload" python -c', smoke)
        self.assertNotIn('docker exec "$workload" python -c', smoke)

    def test_monitor_outage_health_is_explicit(self):
        smoke=(ROOT/"deploy/single/ci-external-guard.sh").read_text()
        self.assertIn("MONITOR_UNAVAILABLE_REPORTS_503",smoke)
        self.assertIn("MONITOR_FALSE_HEALTHY",smoke)
        self.assertIn("e.code == 503",smoke)
        self.assertIn("GUARD_RESTART_RECOVERED",smoke)

    def test_disposable_guard_auto_recovery(self):
        smoke=(ROOT/"deploy/single/ci-external-guard.sh").read_text()
        for marker in ("--restart on-failure:3", "RestartCount", "GUARD_AUTOMATIC_CRASH_RECOVERY_OK", "docker update --restart no"):
            self.assertIn(marker,smoke)
        self.assertIn('docker exec --user 10001:10001 "$workload" python -c',smoke)
        self.assertIn("before_started=",smoke)
        self.assertIn("before_pid=",smoke)
        self.assertIn("after_started=",smoke)
        self.assertIn("after_pid=",smoke)
        self.assertIn("GUARD_INIT_CRASH_SIGNAL_SENT",smoke)
        self.assertIn('sudo kill -KILL "$guard_host_pid"',smoke)
        self.assertNotIn("os.kill(1,signal.SIGKILL)",smoke)

    def test_runtime_ci_enforces_process_capabilities(self):
        smoke=(ROOT/"deploy/single/ci-external-guard.sh").read_text()
        for marker in (
            "EXTERNAL_GUARD_CHILD_IDENTITY_AND_SOCKET_MODE_OK",
            "EXTERNAL_GUARD_PROCESS_BOUNDARY_OK",
            'assert mode == 0o660',
            'assert int(fields["CapEff"][0], 16) == 0',
            'assert int(fields["NoNewPrivs"][0]) == 1',
            'assert not Path("/var/run/docker.sock").exists()',
        ):
            self.assertIn(marker, smoke)

    def test_guard_unix_listener_and_strict_transport(self):
        guard=(ROOT/"backend/app/docker_socket_guard.py").read_text()
        monitor=(ROOT/"backend/app/monitor_agent.py").read_text()
        for src in (guard,monitor):
            ast.parse(src)
            self.assertIn("DOCKER_GUARD_UNIX_SOCKET",src)
        self.assertIn("UnixStreamServer",guard)
        self.assertIn("os.umask(0o117)",guard)
        self.assertIn("UnixHTTPConnection(DOCKER_GUARD_UNIX_SOCKET)",monitor)
        self.assertIn("allowed_target(self.path)",guard)
        self.assertIn("do_DELETE = do_POST",guard)

    def test_single_workload_never_starts_raw_guard_in_external_mode(self):
        supervisor=(ROOT/"deploy/single/supervise.py").read_text()
        health=(ROOT/"deploy/single/health.py").read_text()
        ast.parse(supervisor)
        ast.parse(health)
        self.assertIn("if external_guard:",supervisor)
        self.assertIn('("web", ["nginx", "-g", "daemon off;"], app_identity())',supervisor)
        self.assertIn("specs.insert(0, (\"docker-guard\"",supervisor)
        self.assertIn("external guard requires Unix socket transport",supervisor)
        self.assertIn('OCI_NT_EXTERNAL_GUARD", "0") != "1"',health)

    def test_external_guard_image_nonroot_and_no_other_app(self):
        dockerfile=(ROOT/"deploy/single/Dockerfile.guard").read_text()
        self.assertIn("USER guard",dockerfile)
        self.assertIn("COPY backend/app/docker_socket_guard.py",dockerfile)
        self.assertNotIn("requirements.txt",dockerfile)
        self.assertNotIn("frontend",dockerfile)
        workload_dockerfile=(ROOT/"deploy/single/Dockerfile").read_text()
        self.assertIn("pid /run/nginx/nginx.pid;",workload_dockerfile)
        self.assertIn("mkdir -p /run/nginx",workload_dockerfile)
        self.assertLess(workload_dockerfile.index("mkdir -p /run/nginx"), workload_dockerfile.index("nginx -t"))
        self.assertIn("/var/log/nginx",workload_dockerfile)

if __name__=="__main__":
    unittest.main()
