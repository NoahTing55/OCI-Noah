import asyncio
import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for name in ("backend", "backend.app"):
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(ROOT / name.replace(".", "/"))]
        sys.modules[name] = pkg

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

load("backend.app.oci_operation_leases", "backend/app/oci_operation_leases.py")
bg = load("backend.app.oci_background_leases", "backend/app/oci_background_leases.py")
maintenance = load("durable_maintenance_for_bg", "deploy/durable_maintenance.py")

class BackgroundLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/"app.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,is_secret INTEGER DEFAULT 0,updated_at TEXT)")
            con.execute("CREATE TABLE manual_tasks(id INTEGER,status TEXT)")
            con.execute("CREATE TABLE launch_jobs(id INTEGER,status TEXT)")
        self.leases = sys.modules["backend.app.oci_operation_leases"]

    def count(self):
        with sqlite3.connect(self.db) as con:
            con.execute("BEGIN IMMEDIATE")
            return self.leases.active_lease_count_in_transaction(con)

    async def test_active_coroutine_blocks_gate(self):
        entered=asyncio.Event()
        finish=asyncio.Event()
        async def job():
            entered.set()
            await finish.wait()
            return "ok"
        task=asyncio.create_task(bg.run_with_lease(self.db,kind="launch",task_id=9,coroutine=job()))
        await entered.wait()
        self.assertEqual(self.count(),1)
        with self.assertRaisesRegex(RuntimeError,"OCI HTTP operations still active"):
            maintenance.begin(self.db,operator="tester",reason="release")
        finish.set()
        self.assertEqual(await task,"ok")
        self.assertEqual(self.count(),0)
        maintenance.begin(self.db,operator="tester",reason="release")

    async def test_exception_releases_lease(self):
        async def broken():
            raise ValueError("boom")
        with self.assertRaises(ValueError):
            await bg.run_with_lease(self.db,kind="manual",task_id=11,coroutine=broken())
        self.assertEqual(self.count(),0)

    async def test_active_gate_rejects_task_body(self):
        maintenance.begin(self.db,operator="tester",reason="release")
        touched=False
        async def should_not_run():
            nonlocal touched
            touched=True
        with self.assertRaises(self.leases.OCIAdmissionBlocked):
            await bg.run_with_lease(self.db,kind="manual",task_id=13,coroutine=should_not_run())
        self.assertFalse(touched)


if __name__=="__main__":
    unittest.main()
