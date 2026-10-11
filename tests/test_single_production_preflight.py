import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/"deploy/single/production-preflight.py"
spec=importlib.util.spec_from_file_location("production_preflight",SCRIPT)
preflight=importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)

class ProductionPreflightTests(unittest.TestCase):
    def test_combines_real_modules_without_granting_cutover(self):
        names=("oci-nt-api","oci-nt-web","oci-nt-monitor-agent","oci-nt-docker-guard")
        sha="a"*40
        images={n:"ghcr.io/noahting55/oci-noah-"+("web" if n=="oci-nt-web" else "api")+":sha-"+sha for n in names}
        ids={n:"sha256:"+"b"*64 for n in names}
        plan={"mode":"PLAN_ONLY","release_authorized":False,
              "single_image":"ghcr.io/noahting55/oci-noah-single:sha-"+sha,
              "single_image_id":"sha256:"+"c"*64,"original_images":images,
              "original_image_ids":ids,"schema":7,"docker_socket_gid":997,
              "maintenance_active":False,"recorded_work_drained":True}
        class FakeController:
            def inspect(self,sha,runner,root):
                return plan
        fake=lambda cmd:'[{"Id":"sha256:'+"b"*64+'","Architecture":"amd64"}]'
        real_module=preflight.module
        with patch.object(preflight,"module",side_effect=lambda name:FakeController() if name=="release-controller.py" else real_module(name)):
            value=preflight.report(sha=sha,root=Path("/tmp/test"),runner=fake)
        self.assertTrue(value["rollback_images_present"])
        self.assertFalse(value["release_authorized"])
        self.assertFalse(value["untracked_oci_calls_verified"])
        self.assertFalse(value["production_cutover_allowed"])

    def test_production_apply_is_always_denied(self):
        import subprocess
        proc=subprocess.run(["python3",str(SCRIPT),"--sha","a"*40,"--apply"],capture_output=True,text=True)
        self.assertEqual(proc.returncode,4)
        self.assertIn('"release_authorized": false',proc.stdout.lower())

if __name__=="__main__":
    unittest.main()
