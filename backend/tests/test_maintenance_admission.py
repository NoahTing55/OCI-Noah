"""Admission guard tests: in-process foundation (not a durable release lock)."""
import unittest

from app.maintenance_state import (
    begin, end, status, reject_new_work_during_maintenance,
    MaintenanceAdmissionBlocked,
)


class MaintenanceAdmissionTests(unittest.TestCase):
    def tearDown(self):
        end()

    def test_allows_when_inactive(self):
        end()
        reject_new_work_during_maintenance()

    def test_blocks_when_active(self):
        end()
        self.assertTrue(begin("unit_test"))
        with self.assertRaises(MaintenanceAdmissionBlocked):
            reject_new_work_during_maintenance()
        self.assertEqual(status()["operation"], "unit_test")

    def test_restores_admission_after_end(self):
        end()
        self.assertTrue(begin("unit_test"))
        end()
        reject_new_work_during_maintenance()


if __name__ == "__main__":
    unittest.main()
