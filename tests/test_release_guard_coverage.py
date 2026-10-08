"""Regression inventory of OCI mutation routes covered by maintenance HTTP guard.

This inventory is intentionally explicit; it catches accidental loss of
protection if route prefixes change. It cannot prove that every newly
introduced OCI SDK call is covered (review is still required).
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "maintenance_route_guard", ROOT / "backend/app/maintenance_route_guard.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

MUTATING_OCI_ENDPOINTS = (
    ("POST", "/api/v1/accounts/15/check"),
    ("POST", "/api/v1/bulk/account-checks"),
    ("POST", "/api/v1/tasks/proxy-health"),
    ("POST", "/api/v1/tasks/instance-batch"),
    ("POST", "/api/v1/tasks/17/retry"),
    ("POST", "/api/v1/tasks/17/safe-resume"),
    ("POST", "/api/v1/instances/sync"),
    ("POST", "/api/v1/accounts/15/instances/sync"),
    ("POST", "/api/v1/accounts/15/instances/ocid1.instance.oc1/actions"),
    ("POST", "/api/v1/accounts/15/public-ips/private-ip-01/replace"),
    ("POST", "/api/v1/accounts/15/launch/network/ensure"),
    ("POST", "/api/v1/accounts/15/launch/jobs"),
    ("POST", "/api/v1/settings/account-check-schedule/run-now"),
    ("POST", "/api/v1/proxy-allocation/execute"),
)
DRAIN_OR_READ_ENDPOINTS = (
    ("POST", "/api/v1/tasks/12/cancel"),
    ("POST", "/api/v1/accounts/15/launch/jobs/7/cancel"),
    ("GET", "/api/v1/system/release/runtime-drain"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/system/backups"),
)


class MaintenanceCoverageTests(unittest.TestCase):
    def test_oci_mutations_are_blocked(self):
        for method, path in MUTATING_OCI_ENDPOINTS:
            with self.subTest(path=path):
                self.assertTrue(guard.protected_oci_write(method, path))

    def test_cancel_and_diagnostics_remain_available(self):
        for method, path in DRAIN_OR_READ_ENDPOINTS:
            with self.subTest(path=path):
                self.assertFalse(guard.protected_oci_write(method, path))


if __name__ == "__main__":
    unittest.main()
