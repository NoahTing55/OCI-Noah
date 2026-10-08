from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeDrainEndpointTests(unittest.TestCase):
    def test_requires_authenticated_current_user(self):
        source = (ROOT / "backend/app/main.py").read_text()
        start = source.index('@app.get(f"{settings.api_prefix}/system/release/runtime-drain")')
        tail = source[start:start+360]
        self.assertIn("Depends(get_current_user)", tail)
        self.assertIn("return runtime_drain_snapshot()", tail)

    def test_explicitly_not_release_approval(self):
        source = (ROOT / "backend/app/runtime_drain_snapshot.py").read_text()
        self.assertIn('"release_authorized": False', source)


if __name__ == "__main__":
    unittest.main()
