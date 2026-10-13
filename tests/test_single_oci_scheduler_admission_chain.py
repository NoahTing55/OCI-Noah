"""Prove the declared scheduled account-check call chain reaches durable leases.

Source-level evidence only; does NOT attest running production processes.
"""
import ast
from pathlib import Path
import unittest

APP=Path(__file__).resolve().parents[1]/"backend/app"

def function(path,name):
    src=(APP/path).read_text()
    tree=ast.parse(src)
    nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
    assert len(nodes)==1,(path,name,len(nodes))
    return nodes[0]

def calls(node):
    out=set()
    for item in ast.walk(node):
        if isinstance(item,ast.Call):
            f=item.func
            if isinstance(f,ast.Name):out.add(f.id)
            elif isinstance(f,ast.Attribute):out.add(f.attr)
    return out

class ScheduledOCIAdmissionChain(unittest.TestCase):
    def test_scheduled_loop_uses_same_leased_task_entry_as_manual_trigger(self):
        self.assertIn("_launch",calls(function("account_check_schedule_service.py","_loop")))
        self.assertIn("_launch",calls(function("account_check_schedule_service.py","run_account_check_schedule_now")))
        self.assertIn("start_account_check_task",calls(function("account_check_schedule_service.py","_launch")))
        self.assertIn("_register_task",calls(function("task_service.py","start_account_check_task")))
        self.assertIn("run_with_lease",calls(function("task_service.py","_register_task")))

    def test_release_scheduler_does_not_advance_when_admission_rejected(self):
        node=function("account_check_schedule_service.py","_loop")
        text=ast.get_source_segment((APP/"account_check_schedule_service.py").read_text(),node)
        self.assertIn("maintenance_active(",text)
        self.assertIn("OCIAdmissionBlocked",text)
        self.assertIn("DurableMaintenanceBlocked",text)
        self.assertIn("Preserve overdue slot",text)

    def test_sdk_calls_cannot_be_proven_drained_by_static_chain(self):
        source=(Path(__file__).resolve().parents[1]/"deploy/single/running-runtime-gate.py").read_text()
        self.assertIn('"running_generation_attested": False',source)
        self.assertIn('"production_cutover_allowed": False',source)

if __name__=="__main__":
    unittest.main()
