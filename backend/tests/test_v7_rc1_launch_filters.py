import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class V7RC1LaunchFilterTests(unittest.TestCase):
    def test_backend_allows_only_two_boot_shapes(self) -> None:
        source = (PROJECT_ROOT / "backend/app/launch_service.py").read_text("utf-8")
        tree = ast.parse(source)
        mapping = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "SUPPORTED_LAUNCH_SHAPES":
                        mapping = ast.literal_eval(node.value)
        self.assertEqual(mapping, {
            "ARM": "VM.Standard.A1.Flex",
            "AMD": "VM.Standard.E2.1.Micro",
        })
        self.assertIn('"operating_system": "Canonical Ubuntu"', source)
        self.assertIn('"shape": selected_shape', source)
        self.assertIn('filtered = [item for item in candidates if _is_ubuntu_image(item)]', source)
        self.assertIn('normalized["shape"] = shape', source)
        self.assertIn('normalized["ocpus"] = None', source)

    def test_api_does_not_accept_arbitrary_shape(self) -> None:
        router = (PROJECT_ROOT / "backend/app/launch_router.py").read_text("utf-8")
        self.assertIn('architecture: Literal["ARM", "AMD"]', router)
        self.assertNotIn('shape: str = Field', router)
        self.assertIn('architecture=architecture', router)

    def test_frontend_only_shows_arm_amd_and_ubuntu(self) -> None:
        app = (PROJECT_ROOT / "frontend/app.js").read_text("utf-8")
        self.assertIn('ARM · VM.Standard.A1.Flex', app)
        self.assertIn('AMD · VM.Standard.E2.1.Micro', app)
        self.assertIn('Ubuntu 镜像', app)
        self.assertNotIn('id="tenant-launch-shape" required disabled', app)
        self.assertIn('architecture: $("tenant-launch-architecture").value', app)
        self.assertNotIn('shape: $("tenant-launch-shape").value', app)


if __name__ == "__main__":
    unittest.main(verbosity=2)
