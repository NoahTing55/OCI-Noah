"""Candidate mode must not spawn the OCI account-check scheduler."""
import ast
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
SOURCE=ROOT/"backend/app/main.py"

class CandidateSchedulerGate(unittest.TestCase):
    def test_scheduler_only_starts_outside_candidate_mode(self):
        tree=ast.parse(SOURCE.read_text(encoding="utf-8"))
        lifespan=next(node for node in ast.walk(tree) if isinstance(node,ast.AsyncFunctionDef) and node.name=="lifespan")
        calls=[node for node in ast.walk(lifespan) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=="start_account_check_scheduler"]
        self.assertEqual(len(calls),1)
        call=calls[0]
        guarded=False
        for node in ast.walk(lifespan):
            if not isinstance(node,ast.If) or call not in list(ast.walk(ast.Module(body=node.body,type_ignores=[]))):
                continue
            test=node.test
            if (isinstance(test,ast.UnaryOp) and isinstance(test.op,ast.Not)
                and isinstance(test.operand,ast.Attribute)
                and isinstance(test.operand.value,ast.Name)
                and test.operand.value.id=="settings"
                and test.operand.attr=="candidate_mode"):
                guarded=True
        self.assertTrue(guarded,"OCI scheduler is not guarded by !settings.candidate_mode")

if __name__=="__main__":
    unittest.main()
