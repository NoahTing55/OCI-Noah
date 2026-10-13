"""Fail closed on unsafe external-Guard release manifests; never authorize cutover."""
import copy
import importlib.util
from pathlib import Path
import unittest

file=Path(__file__).resolve().parents[1]/"deploy/single/validate-external-guard-rc.py"
spec=importlib.util.spec_from_file_location("external_guard_manifest",file)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def good():
    image="ghcr.io/noahting55/oci-noah-single@sha256:"+"a"*64
    return {
        "name":"oci-nt-rc-offline-validation",
        "services":{
            "guard-socket-init":{"restart":"no","network_mode":"none"},
            "docker-guard":{
                "image":"ghcr.io/noahting55/oci-noah-guard@sha256:"+"b"*64,
                "network_mode":"none","restart":"unless-stopped",
                "cap_drop":["ALL"],"security_opt":["no-new-privileges:true"],
                "volumes":[{"source":"/var/run/docker.sock","target":"/var/run/docker.sock","read_only":True}]
            },
            "all-in-one":{
                "image":image,"network_mode":"host","restart":"unless-stopped",
                "cap_drop":["ALL"],"cap_add":["SETUID","SETGID","KILL"],
                "security_opt":["no-new-privileges:true"],
                "environment":{"OCI_NT_EXTERNAL_GUARD":"1","DOCKER_GUARD_UNIX_SOCKET":"/run/oci-guard/guard.sock"},
                "volumes":[{"source":"guard-socket","target":"/run/oci-guard","read_only":True}]
            }
        }
    }

class RCManifest(unittest.TestCase):
    def test_valid_contract_still_cannot_authorize_cutover(self):
        self.assertEqual(mod.validate(good()),[])

    def test_mutations_fail_closed(self):
        changes=[
            lambda d:d["services"]["all-in-one"].update({"image":"latest"}),
            lambda d:d["services"]["all-in-one"].update({"build":{"context":"."}}),
            lambda d:d["services"]["all-in-one"].update({"group_add":["997"]}),
            lambda d:d["services"]["all-in-one"]["volumes"].append({"source":"/var/run/docker.sock","target":"/var/run/docker.sock","read_only":True}),
            lambda d:d["services"]["all-in-one"]["volumes"][0].update({"read_only":False}),
            lambda d:d["services"]["docker-guard"].update({"restart":"no"}),
            lambda d:d["services"]["docker-guard"].update({"network_mode":"host"}),
            lambda d:d["services"]["docker-guard"].update({"image":"ghcr.io/noahting55/oci-noah-guard:latest"}),
            lambda d:d["services"]["all-in-one"].update({"privileged":True}),
            lambda d:d["services"]["all-in-one"].update({"cap_add":["ALL"]}),
            lambda d:d.update({"name":"oci-nt"}),
            lambda d:d["services"]["all-in-one"]["environment"].update({"OCI_NT_EXTERNAL_GUARD":"0"}),
        ]
        for i, change in enumerate(changes):
            with self.subTest(i=i):
                d=copy.deepcopy(good())
                change(d)
                self.assertTrue(mod.validate(d))

    def test_missing_guard_or_unexpected_service_rejected(self):
        d=good();d["services"].pop("docker-guard")
        self.assertTrue(mod.validate(d))
        d=good();d["services"]["unrelated"]={}
        self.assertTrue(mod.validate(d))

if __name__=="__main__":
    unittest.main()
