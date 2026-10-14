"""AST audit: direct OCI service calls reachable from API routes must have HTTP leases.

Tracks calls to explicitly imported OCI service entrypoints and same-file helper
functions. This is deliberately not an exhaustive whole-repository call graph.
"""
import ast
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
MAIN=ROOT/"backend/app/main.py"
GUARD=ROOT/"backend/app/maintenance_route_guard.py"
spec=importlib.util.spec_from_file_location("direct_oci_guard",GUARD)
guard=importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

OCI_MODULES={"oci_service","oci_resource_service"}
source=MAIN.read_text()
tree=ast.parse(source)
imports={
    alias.asname or alias.name for node in tree.body
    if isinstance(node,ast.ImportFrom) and node.module
    and node.module.lstrip(".") in OCI_MODULES
    for alias in node.names
}
funcs={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}

def call_names(node):
    return {n.func.id for n in ast.walk(node)
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}

def route_defs(node):
    for dec in node.decorator_list:
        if not isinstance(dec,ast.Call) or not isinstance(dec.func,ast.Attribute):
            continue
        if not isinstance(dec.func.value,ast.Name) or dec.func.value.id!="app":
            continue
        method=dec.func.attr.upper()
        if method not in {"GET","POST","PUT","PATCH","DELETE"} or not dec.args:
            continue
        arg=dec.args[0]
        if not isinstance(arg,ast.JoinedStr):
            continue
        pieces=[]
        for part in arg.values:
            if isinstance(part,ast.Constant) and isinstance(part.value,str):
                pieces.append(part.value)
            elif isinstance(part,ast.FormattedValue):
                pieces.append("/api/v1")
            else:
                raise AssertionError("unexpected route expression")
        yield method,"".join(pieces)

def reaches_oci(name,seen=None):
    if name in imports:
        return True
    if name not in funcs or name in (seen or set()):
        return False
    seen=(seen or set()) | {name}
    return any(reaches_oci(c,seen) for c in call_names(funcs[name]))

class DirectOCIServiceRouteAudit(unittest.TestCase):
    def test_known_service_imports_remain_detected(self):
        self.assertTrue({"check_oci_credentials","instance_action",
                         "list_instances_all_regions","replace_ephemeral_public_ip"} <= imports)

    def test_every_direct_or_local_helper_oci_route_has_durable_http_lease(self):
        checked=[]
        for name,node in funcs.items():
            routes=list(route_defs(node))
            if not routes or not reaches_oci(name):
                continue
            for method,path in routes:
                checked.append((method,path,name))
                with self.subTest(method=method,path=path,handler=name):
                    self.assertTrue(guard.protected_oci_request(method,path),
                                    "OCI service call route bypasses durable HTTP lease")
        self.assertGreaterEqual(len(checked),5,checked)

    def test_audit_does_not_claim_production_global_quiescence(self):
        gate=(ROOT/"deploy/single/running-runtime-gate.py").read_text()
        self.assertIn('"production_cutover_allowed": False',gate)

if __name__=="__main__":
    unittest.main()
