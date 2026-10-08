"""Source-level regression for OCI runtime drain introspection imports."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeSnapshotTests(unittest.TestCase):
    def test_all_background_task_registries_covered(self):
        content = (ROOT / "backend/app/runtime_drain_snapshot.py").read_text()
        for name in ("task_service._tasks", "instance_batch_service._runtime_tasks",
                     "launch_task_service._tasks", "launch_task_service._notification_tasks",
                     'http["inflight_count"]', '"release_authorized": False'):
            with self.subTest(name=name):
                self.assertIn(name, content)


if __name__ == "__main__":
    unittest.main()
