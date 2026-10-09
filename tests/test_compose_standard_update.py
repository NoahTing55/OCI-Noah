"""Standard Docker Compose update must not mix backend and Web tags."""
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class ComposeUpdateTests(unittest.TestCase):
    def test_api_and_workers_use_backend_only(self):
        source=(ROOT/"docker-compose.yml").read_text()
        self.assertEqual(source.count("OCI_NOAH_BACKEND_TAG"),3)
        self.assertEqual(source.count("OCI_NOAH_WEB_TAG"),1)
        self.assertNotIn("OCI_NOAH_IMAGE_TAG",source)
        self.assertNotIn(":-latest}",source)
    def test_existing_production_images_are_safe_defaults(self):
        source=(ROOT/"docker-compose.yml").read_text()
        self.assertIn("OCI_NOAH_BACKEND_TAG:-sha-9d64b5da8f2c0252a36a42074ff471ef1f08f820",source)
        self.assertIn("OCI_NOAH_WEB_TAG:-sha-fd6a7e7b030a4bae2cad147fc4967c4fd7c87a7a",source)

if __name__=="__main__":
    unittest.main()
