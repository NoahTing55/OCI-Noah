import importlib.util
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tracker", ROOT / "backend/app/oci_http_inflight.py")
tracker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracker)


class InflightTests(unittest.TestCase):
    def test_counter_recovers_from_success_and_exception(self):
        self.assertEqual(tracker.snapshot()["inflight_count"], 0)
        with tracker.track_oci_http_write("/api/v1/instances/sync"):
            self.assertEqual(tracker.snapshot()["inflight_count"], 1)
            self.assertFalse(tracker.snapshot()["release_authorized"])
        self.assertEqual(tracker.snapshot()["inflight_count"], 0)
        with self.assertRaises(ValueError):
            with tracker.track_oci_http_write("/api/v1/accounts/1/actions"):
                raise ValueError("failure")
        self.assertEqual(tracker.snapshot()["inflight_count"], 0)

    def test_concurrent_tracking(self):
        barrier = threading.Barrier(3)
        release = threading.Event()
        errors = []
        def worker():
            try:
                with tracker.track_oci_http_write("/api/v1/instances/sync"):
                    barrier.wait(timeout=3)
                    if not release.wait(timeout=3):
                        raise RuntimeError("timeout")
            except BaseException as exc:
                errors.append(str(exc))
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        try:
            barrier.wait(timeout=3)
            self.assertEqual(tracker.snapshot()["inflight_count"], 2)
        finally:
            release.set()
            for thread in threads:
                thread.join(4)
        self.assertFalse(errors, errors)
        self.assertEqual(tracker.snapshot()["inflight_count"], 0)


if __name__ == "__main__":
    unittest.main()
