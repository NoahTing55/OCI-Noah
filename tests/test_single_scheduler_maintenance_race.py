"""Scheduled OCI checks must not skip a slot when maintenance wins admission."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"backend/app/account_check_schedule_service.py"

class ScheduledMaintenanceRaceContracts(unittest.TestCase):
    def test_admission_before_schedule_advance(self):
        src=SOURCE.read_text(encoding="utf-8")
        block=src.split("if due is not None and due <= _now():",1)[1]
        self.assertLess(block.index("await _launch("),block.index('config["next_run_at"] = _iso(_next(config))'))
        self.assertIn("DurableMaintenanceBlocked",block)
        self.assertIn("OCIAdmissionBlocked",block)
        self.assertIn("MaintenanceAdmissionBlocked",block)
        self.assertIn("Preserve overdue slot",block)

if __name__=="__main__":
    unittest.main()
