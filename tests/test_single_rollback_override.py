import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

P=Path(__file__).resolve().parents[1]/"deploy/single/render-four-rollback.py"
spec=importlib.util.spec_from_file_location("four_rollback_render",P)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SHA="a"*40
def evidence():
    return {"manifest_version":1,"release_authorized":False,
            "original_four_images":{
                "oci-nt-api":"ghcr.io/noahting55/oci-noah-api:sha-"+SHA,
                "oci-nt-monitor-agent":"ghcr.io/noahting55/oci-noah-api:sha-"+SHA,
                "oci-nt-docker-guard":"ghcr.io/noahting55/oci-noah-api:sha-"+SHA,
                "oci-nt-web":"ghcr.io/noahting55/oci-noah-web:sha-"+SHA}}

class RollbackOverrideTests(unittest.TestCase):
    def test_all_images_explicitly_pinned(self):
        text=mod.render(evidence())
        self.assertEqual(text.count("    image:"),4)
        self.assertEqual(text.count(":sha-"+SHA),4)
        self.assertNotIn("oci--nt",text)
        self.assertNotIn("docker compose",text)
    def test_foreign_image_rejected(self):
        e=evidence()
        e["original_four_images"]["oci-nt-web"]="web:latest"
        with self.assertRaises(ValueError):
            mod.render(e)
    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            out=Path(t)/"rollback.yml"
            mod.write_new(out,mod.render(evidence()))
            self.assertEqual(out.stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):
                mod.write_new(out,mod.render(evidence()))
    def test_missing_service_rejected(self):
        e=evidence()
        del e["original_four_images"]["oci-nt-api"]
        with self.assertRaises(ValueError):
            mod.render(e)

if __name__=="__main__":
    unittest.main()
