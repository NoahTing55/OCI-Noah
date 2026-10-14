from pathlib import Path
import unittest

FILE=Path(__file__).resolve().parents[1]/"deploy/compose.single-offline-review.yml"

class OfflineReviewManifest(unittest.TestCase):
    def test_offline_manifest_has_no_production_contact(self):
        text=FILE.read_text()
        import yaml
        data=yaml.safe_load(text)
        self.assertEqual(set(data["services"]),{"all-in-one","guard-image-inspect"})
        for name,service in data["services"].items():
            self.assertEqual(service["network_mode"],"none")
            self.assertEqual(service["restart"],"no")
            self.assertTrue(service["read_only"])
            self.assertEqual(service["cap_drop"],["ALL"])
            self.assertNotIn("volumes",service)
            self.assertNotIn("ports",service)
            self.assertNotIn("env_file",service)
            self.assertNotIn("environment",service)
            self.assertNotIn("build",service)
            self.assertNotIn("docker.sock",str(service))
            self.assertRegex(service["image"],r"@sha256:[0-9a-f]{64}$")
            self.assertEqual(service["entrypoint"],["python","-c"])
        self.assertNotIn("network_mode: host",text)

if __name__=="__main__": unittest.main()
