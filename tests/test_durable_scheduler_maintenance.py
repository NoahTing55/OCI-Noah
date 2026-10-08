"""Guardrail test that the scheduled OCI check respects durable release maintenance."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SchedulerMaintenanceTests(unittest.TestCase):
    def test_persistent_gate_before_schedule_advance(self):
        content = (ROOT / "backend/app/account_check_schedule_service.py").read_text()
        body = content.split("async def _loop() -> None:", 1)[1].split(
            "def start_account_check_scheduler()", 1
        )[0]
        marker = body.index("if maintenance_active(settings.db_path):")
        schedule_read = body.index("config = _read()")
        launch = body.index("await _launch(")
        self.assertLess(marker, schedule_read)
        self.assertLess(marker, launch)
        self.assertIn("await asyncio.sleep(15)\n                continue", body[marker:schedule_read])


if __name__ == "__main__":
    unittest.main()
