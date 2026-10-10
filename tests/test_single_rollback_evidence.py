import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "deploy/single/rollback-evidence.py"
spec = importlib.util.spec_from_file_location("oci_rollback_evidence", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SHA = "a"*40

def report():
    return {"mode":"PLAN_ONLY","release_authorized":False,
            "original_images":{
                "oci-nt-api":"ghcr.io/noahting55/oci-noah-api:sha-"+SHA,
                "oci-nt-web":"ghcr.io/noahting55/oci-noah-web:sha-"+SHA,
                "oci-nt-monitor-agent":"ghcr.io/noahting55/oci-noah-api:sha-"+SHA,
                "oci-nt-docker-guard":"ghcr.io/noahting55/oci-noah-api:sha-"+SHA},
            "single_image":"ghcr.io/noahting55/oci-noah-single:sha-"+SHA,
            "single_image_id":"sha256:synthetic",
            "schema":7,"docker_socket_gid":997}

class RollbackEvidenceTests(unittest.TestCase):
    def test_safe_inventory_contains_no_secrets(self):
        item=module.safe_manifest(report())
        self.assertFalse(item["release_authorized"])
        self.assertFalse(item["contains_database_or_secrets"])
        self.assertEqual(len(item["original_four_images"]),4)
        self.assertNotIn(".env",json.dumps(item))
        self.assertNotIn("oci-nt-single-lab",json.dumps(item))

    def test_foreign_container_rejected(self):
        item=report()
        item["original_images"]["oci--nt-app-1"]="foreign:latest"
        with self.assertRaises(ValueError):
            module.safe_manifest(item)

    def test_unpinned_rollback_refused(self):
        item=report()
        item["original_images"]["oci-nt-api"]="latest"
        with self.assertRaises(ValueError):
            module.safe_manifest(item)

    def test_overwrite_refused_and_private_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"manifest.json"
            module.write_exclusive(path, module.safe_manifest(report()))
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):
                module.write_exclusive(path, module.safe_manifest(report()))
            self.assertFalse(json.loads(path.read_text())["release_authorized"])

if __name__ == "__main__":
    unittest.main()
