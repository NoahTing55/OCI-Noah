import importlib.util
import json
import unittest
from pathlib import Path

p=Path(__file__).resolve().parents[1]/"deploy/single/rollback-image-gate.py"
spec=importlib.util.spec_from_file_location("rollback_gate",p)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sha="a"*40
imageid="sha256:"+"b"*64
def manifest():
    return {"manifest_version":1,"release_authorized":False,
        "original_four_images":{
          "oci-nt-api":"ghcr.io/noahting55/oci-noah-api:sha-"+sha,
          "oci-nt-web":"ghcr.io/noahting55/oci-noah-web:sha-"+sha,
          "oci-nt-monitor-agent":"ghcr.io/noahting55/oci-noah-api:sha-"+sha,
          "oci-nt-docker-guard":"ghcr.io/noahting55/oci-noah-api:sha-"+sha}}

class RollbackImageGateTests(unittest.TestCase):
    def test_present_images_are_not_release_authorization(self):
        output=m.verify(manifest(),lambda cmd:json.dumps([{"Id":imageid,"Architecture":"amd64"}]))
        self.assertEqual(len(output["original_image_ids"]),4)
        self.assertFalse(output["release_authorized"])
        self.assertFalse(output["image_restore_tested"])
    def test_missing_local_image_fails(self):
        with self.assertRaises(ValueError):
            m.verify(manifest(),lambda cmd:"[]")
    def test_foreign_image_fails(self):
        item=manifest()
        item["original_four_images"]["oci-nt-api"]="ghcr.io/attacker/image:latest"
        with self.assertRaises(ValueError):
            m.verify(item,lambda cmd:json.dumps([{"Id":imageid,"Architecture":"amd64"}]))
    def test_missing_service_fails(self):
        item=manifest()
        del item["original_four_images"]["oci-nt-web"]
        with self.assertRaises(ValueError):
            m.verify(item,lambda cmd:"[]")

if __name__=="__main__":
    unittest.main()
