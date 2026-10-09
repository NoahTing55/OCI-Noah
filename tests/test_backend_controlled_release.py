import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("release_backend",ROOT/"deploy/release-backend.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class BackendReleaseSafetyTests(unittest.TestCase):
    def test_sha_validation_before_any_system_call(self):
        with self.assertRaises(ValueError):
            mod.plan("latest",run=lambda *a,**kw:self.fail("must not run"))

    def test_never_exposes_apply(self):
        script=(ROOT/"deploy/release-backend.py").read_text()
        self.assertIn('if args.apply:',script)
        self.assertIn('"execution_supported":False',script)
        self.assertIn('"release_authorized":False',script)
        self.assertNotIn('"up","-d"',script)
        self.assertNotIn('"docker","pull"',script)

    def test_backend_override_does_not_target_web(self):
        text=(ROOT/"deploy/compose.backend-release.yml").read_text()
        self.assertIn("api:",text)
        self.assertIn("docker-guard:",text)
        self.assertIn("monitor:",text)
        self.assertNotIn("  web:",text)
        self.assertIn("OCI_NT_RELEASE_BACKEND_IMAGE",text)

    def test_plan_from_mocked_docker_is_non_mutating(self):
        sha="a"*40
        with tempfile.TemporaryDirectory() as root:
            paths=[ROOT/".env",ROOT/"docker-compose.yml",ROOT/"deploy/compose.backend-release.yml"]
            original=Path.is_file
            def exists(p):
                if str(p)==str(ROOT/".env"): return True
                return original(p)
            calls=[]
            def fake(args,env=None):
                calls.append(args)
                if args[:2]==["docker","compose"]:
                    return json.dumps({"services":{
                        "api":{"image":mod.IMAGE+sha},
                        "monitor":{"image":mod.IMAGE+sha},
                        "docker-guard":{"image":mod.IMAGE+sha},
                        "web":{"image":"ghcr.io/noahting55/oci-noah-web:sha-old"}
                    }})
                if args[:2]==["docker","inspect"]:
                    return json.dumps([{"Config":{"Image":"old-image","Labels":{"com.docker.compose.project":"oci-nt"}},
                                        "State":{"Health":{"Status":"healthy"}}}])
                if args[:3]==["docker","image","inspect"]:
                    return json.dumps([{"Id":"sha256:test"}])
                self.fail("Unexpected command "+repr(args))
            with patch.object(Path,"is_file",exists):
                report=mod.plan(sha,run=fake)
            self.assertFalse(report["release_authorized"])
            self.assertFalse(report["execution_supported"])
            self.assertTrue(report["web_unmodified"])
            self.assertEqual(len(calls),6)
            self.assertTrue(all("up" not in a and "stop" not in a and "pull" not in a for a in calls))

if __name__=="__main__":
    unittest.main()
