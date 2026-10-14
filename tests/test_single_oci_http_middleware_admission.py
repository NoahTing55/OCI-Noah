"""Execute the real FastAPI maintenance middleware with isolated SQLite state.

The middleware AST is extracted from production source to avoid importing the
full production application/settings. No OCI SDK or real credentials are used.
"""
import ast
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from app import oci_operation_leases as leases
from app import maintenance_route_guard as guard

MAIN=Path(__file__).resolve().parents[1]/"backend/app/main.py"


def load_real_middleware(namespace):
    tree=ast.parse(MAIN.read_text())
    matches=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
             and n.name=="maintenance_request_guard"]
    assert len(matches)==1
    node=matches[0]
    node.decorator_list=[]
    compiled=compile(ast.Module(body=[node],type_ignores=[]),str(MAIN),"exec")
    exec(compiled,namespace)
    return namespace[node.name]


class MiddlewareHTTPAdmission(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db=Path(temp.name)/"isolated.sqlite"
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,is_secret INTEGER DEFAULT 0,updated_at TEXT)")
        self.db=db
        self.calls=[]
        self.state={"active":False}
        @contextmanager
        def count_http(path):
            yield
        namespace={
            "Request":Request,"JSONResponse":JSONResponse,"sqlite3":sqlite3,
            "settings":types.SimpleNamespace(db_path=db,api_prefix="/api/v1"),
            "maintenance_status":lambda:self.state,
            "protected_oci_request":guard.protected_oci_request,
            "maintenance_active":guard.maintenance_active,
            "acquire_oci_lease":leases.acquire,
            "release_oci_lease":leases.release,
            "OCIAdmissionBlocked":leases.OCIAdmissionBlocked,
            "track_oci_http_write":count_http,
        }
        middleware=load_real_middleware(namespace)
        app=FastAPI()
        app.middleware("http")(middleware)
        @app.post("/api/v1/accounts")
        def account_creation():
            self.calls.append("create")
            return {"ok":True}
        @app.get("/api/v1/instances")
        def instance_list():
            self.calls.append("instances")
            return {"ok":True}
        @app.get("/api/v1/health")
        def health():
            return {"ok":True}
        self.client=TestClient(app)
        self.addCleanup(self.client.close)

    def marker(self,active):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO system_settings(setting_key,setting_value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
                        (leases.MARKER,'{"active":true}' if active else '{"active":false}'))

    def test_real_http_requests_blocked_and_restored(self):
        self.marker(True)
        self.assertEqual(self.client.post("/api/v1/accounts").status_code,503)
        self.assertEqual(self.client.get("/api/v1/instances").status_code,503)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.client.get("/api/v1/health").status_code,200)
        self.marker(False)
        self.assertEqual(self.client.post("/api/v1/accounts").status_code,200)
        self.assertEqual(self.client.get("/api/v1/instances").status_code,200)
        self.assertEqual(self.calls,["create","instances"])

    def test_missing_database_blocks_protected_http_only(self):
        self.db.unlink()
        self.assertEqual(self.client.post("/api/v1/accounts").status_code,503)
        self.assertEqual(self.client.get("/api/v1/instances").status_code,503)
        self.assertEqual(self.client.get("/api/v1/health").status_code,200)
        self.assertEqual(self.calls,[])

    def test_live_local_database_maintenance_blocks_mutations(self):
        self.state["active"]=True
        self.assertEqual(self.client.post("/api/v1/accounts").status_code,503)
        self.assertEqual(self.client.get("/api/v1/health").status_code,200)
        self.assertEqual(self.calls,[])

if __name__=="__main__":
    unittest.main()
