import importlib.util
import unittest
from pathlib import Path

p=Path(__file__).resolve().parents[1]/"deploy/single/validate-four-rollback.py"
spec=importlib.util.spec_from_file_location("validate_four_rollback",p)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
S="a"*40
def fixture():
    names=m.SERVICES
    images={v:"ghcr.io/noahting55/oci-noah-"+("web" if k=="web" else "api")+":sha-"+S for k,v in names.items()}
    services={k:{"container_name":v,"network_mode":"host","image":images[v]} for k,v in names.items()}
    return services,{"original_four_images":images}

class ValidateComposeTests(unittest.TestCase):
    def test_valid_plan_not_authorized(self):
        services,e=fixture()
        result=m.validate(services,e)
        self.assertEqual(result["original_images_checked"],4)
        self.assertFalse(result["release_authorized"])
    def test_default_old_api_image_rejected(self):
        services,e=fixture()
        services["api"]["image"]="ghcr.io/noahting55/oci-noah-api:latest"
        with self.assertRaises(ValueError):
            m.validate(services,e)
    def test_wrong_project_container_rejected(self):
        services,e=fixture()
        services["api"]["container_name"]="oci--nt-app-1"
        with self.assertRaises(ValueError):
            m.validate(services,e)
    def test_extra_service_rejected(self):
        services,e=fixture()
        services["unrelated"]={}
        with self.assertRaises(ValueError):
            m.validate(services,e)

if __name__=="__main__":
    unittest.main()
