import importlib.util
import json
import unittest
from pathlib import Path

P=Path(__file__).resolve().parents[1]/"deploy/single/docker-scope-adapter.py"
spec=importlib.util.spec_from_file_location("docker_scope_adapter",P)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def response(argv, *, project="oci-nt", foreign_mount=False):
    name=argv[-1]
    service={"oci-nt-api":"api","oci-nt-web":"web",
             "oci-nt-monitor-agent":"monitor","oci-nt-docker-guard":"docker-guard"}[name]
    return json.dumps([{"Name":"/"+name,"Id":"a"*64,"Image":"sha256:"+"b"*64,
        "Config":{"Labels":{"com.docker.compose.project":project,
                             "com.docker.compose.service":service}},
        "Mounts":[{"Source":"/opt/oci--nt/data"}] if foreign_mount else []}])

class ScopeTests(unittest.TestCase):
    def test_exact_four_verified_without_mutations(self):
        commands=[]
        def run(argv):
            commands.append(argv)
            return response(argv)
        report=m.validate_four(run)
        self.assertEqual(report["status"],"FOUR_SCOPE_VERIFIED")
        self.assertFalse(report["release_authorized"])
        self.assertEqual(len(commands),4)
        self.assertTrue(all(x[:2]==["docker","inspect"] for x in commands))
    def test_foreign_compose_ownership_blocked(self):
        with self.assertRaises(m.ScopeError):
            m.validate_four(lambda argv:response(argv,project="oci--nt"))
    def test_foreign_mount_blocked(self):
        with self.assertRaises(m.ScopeError):
            m.validate_four(lambda argv:response(argv,foreign_mount=True))
    def test_lifecycle_always_blocked(self):
        with self.assertRaises(m.ScopeError):
            m.lifecycle("stop_four")

if __name__=="__main__":
    unittest.main()
