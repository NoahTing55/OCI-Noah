"""Fail closed if known cross-module OCI worker launch sites lose durable leases.

Tracks account checks, batch operations, instance launches and launch-success
public-IP discovery. This is scoped evidence, not global SDK quiescence proof.
"""
import ast
from pathlib import Path
import unittest

APP=Path(__file__).resolve().parents[1]/"backend/app"
WORKERS={
    "task_service.py":{"manual"},
    "instance_batch_service.py":{"instance_batch"},
    "launch_task_service.py":{"launch","launch_notification"},
}
CROSS_MODULE_IMPORTS={
    "task_service.py":("account_check_service","execute_account_check"),
    "instance_batch_service.py":("oci_service","instance_action"),
    "launch_task_service.py":("launch_service","launch_one_instance"),
}
class CrossModuleWorkerAudit(unittest.TestCase):
    def test_each_asyncio_spawn_in_oci_worker_modules_uses_lease(self):
        for filename, expected_kinds in WORKERS.items():
            with self.subTest(module=filename):
                tree=ast.parse((APP/filename).read_text())
                launched=[]
                for node in ast.walk(tree):
                    if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute):
                        continue
                    if not (isinstance(node.func.value,ast.Name) and node.func.value.id=="asyncio"
                            and node.func.attr=="create_task"):
                        continue
                    self.assertEqual(len(node.args),1)
                    worker=node.args[0]
                    self.assertIsInstance(worker,ast.Call)
                    self.assertIsInstance(worker.func,ast.Name)
                    self.assertEqual(worker.func.id,"run_with_lease",
                                     f"{filename}:{node.lineno}: unleased asyncio.create_task")
                    kind=next((kw.value.value for kw in worker.keywords
                               if kw.arg=="kind" and isinstance(kw.value,ast.Constant)),None)
                    self.assertIn(kind,expected_kinds)
                    launched.append(kind)
                self.assertEqual(set(launched),expected_kinds)

    def test_cross_module_oci_imports_still_checked(self):
        for filename,(module,symbol) in CROSS_MODULE_IMPORTS.items():
            tree=ast.parse((APP/filename).read_text())
            # Presence is tested explicitly; no unused expectation may silently vanish.
            self.assertTrue(any(isinstance(n,ast.ImportFrom) and n.module==module
                                and any(a.name==symbol for a in n.names)
                                for n in tree.body),f"{filename}: lost {module}.{symbol}")

    def test_launch_notification_performs_sdk_lookup_under_lease(self):
        tree=ast.parse((APP/"launch_task_service.py").read_text())
        funcs={x.name:x for x in tree.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))}
        notify=funcs["_notify_launch_instance_success"]
        names={n.func.id for n in ast.walk(notify) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
        self.assertIn("resolve_instance_public_ip",names)
        spawn=funcs["_spawn_launch_success_notification"]
        calls={n.func.id for n in ast.walk(spawn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
        self.assertIn("run_with_lease",calls)

if __name__=="__main__":
    unittest.main()
