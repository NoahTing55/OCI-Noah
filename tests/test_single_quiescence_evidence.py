"""CI-only regression tests for reported OCI writer quiescence evidence."""
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/"deploy/single/quiescence_evidence.py"
spec=importlib.util.spec_from_file_location("quiescence_evidence",path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

WRITERS=("api-mutations","scheduled-jobs","background-worker")
NOW=10000

def sample():
    return {
        "scope":"deployment-test-only",
        "observed_at":NOW,
        "writers":[{"id":name,"admission_blocked":True,
                    "worker_stopped":True,"in_flight":0} for name in WRITERS],
    }

def validate(value, **kw):
    return module.validate_quiescence_evidence(
        value,expected_writers=kw.get("expected_writers",WRITERS),
        expected_scope="deployment-test-only",now_epoch=NOW)

class QuiescenceProofContract(unittest.TestCase):
    def test_consistent_evidence_is_not_deployment_authorization(self):
        self.assertEqual(validate(sample()),
                         {"evidence_consistent":True,"production_authorized":False})

    def test_missing_and_duplicate_writers_rejected(self):
        for changed in ("missing","duplicate","extra"):
            with self.subTest(changed=changed):
                e=sample()
                if changed=="missing": e["writers"].pop()
                elif changed=="duplicate": e["writers"][0]=e["writers"][1].copy()
                else: e["writers"].append({"id":"unknown","admission_blocked":True,
                                          "worker_stopped":True,"in_flight":0})
                with self.assertRaises(module.QuiescenceRejected):validate(e)

    def test_any_open_gate_or_active_process_rejected(self):
        for key,value in (("admission_blocked",False),("worker_stopped",False),
                          ("in_flight",1),("in_flight",False)):
            with self.subTest(key=key,value=value):
                e=sample()
                e["writers"][0][key]=value
                with self.assertRaises(module.QuiescenceRejected):validate(e)

    def test_stale_future_wrong_scope_and_missing_inventory_rejected(self):
        for key,value in (("observed_at",NOW-31),("observed_at",NOW+1),
                          ("observed_at",True),("scope","other")):
            with self.subTest(key=key):
                e=sample();e[key]=value
                with self.assertRaises(module.QuiescenceRejected):validate(e)
        with self.assertRaises(module.QuiescenceRejected):
            validate(sample(),expected_writers=())

    def test_unexpected_fields_and_malformed_payload_rejected(self):
        for field in ("unknown","writers"):
            e=sample()
            if field=="unknown":e["extra"]="ignored?"
            else:e["writers"][0]["extra"]="ignored?"
            with self.subTest(field=field):
                with self.assertRaises(module.QuiescenceRejected):validate(e)
        with self.assertRaises(module.QuiescenceRejected):validate(None)

if __name__=="__main__":
    unittest.main()
