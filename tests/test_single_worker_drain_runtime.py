"""Behavioral tests for graceful OCI worker shutdown.

These tests exercise asyncio tasks rather than matching source text.
"""
import asyncio
import os
from cryptography.fernet import Fernet
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
for key in ("ACCESS_TOKEN_MINUTES", "BULK_CHECK_INTERVAL_SECONDS"):
    if not os.environ.get(key, "").strip():
        os.environ[key] = "30"
os.environ["CREDENTIAL_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
sys.path.insert(0,str(ROOT/"backend"))
from app.oci_background_leases import await_registered_workers
from app import task_service,instance_batch_service,launch_task_service

class WorkerDrainRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_pools(self):
        with patch.dict(task_service._tasks,{},clear=True),patch.dict(instance_batch_service._runtime_tasks,{},clear=True),patch.dict(launch_task_service._tasks,{},clear=True),patch.object(launch_task_service,"_notification_tasks",set()):
            report=await await_registered_workers(timeout_seconds=0.01)
            self.assertTrue(report["complete"])
            self.assertEqual(report["observed"],0)

    async def test_timeout_leaves_real_work_running(self):
        started=asyncio.Event()
        release=asyncio.Event()
        async def worker():
            started.set()
            await release.wait()
        task=asyncio.create_task(worker())
        await started.wait()
        with patch.dict(task_service._tasks,{1:task},clear=True),patch.dict(instance_batch_service._runtime_tasks,{},clear=True),patch.dict(launch_task_service._tasks,{},clear=True),patch.object(launch_task_service,"_notification_tasks",set()):
            report=await await_registered_workers(timeout_seconds=0.01)
            self.assertFalse(report["complete"])
            self.assertEqual(report["remaining"],1)
            self.assertFalse(task.cancelled())
            self.assertFalse(task.done())
            release.set()
            await task
            report=await await_registered_workers(timeout_seconds=0.01)
            self.assertTrue(report["complete"])

    async def test_notification_pool_is_counted(self):
        async def worker():
            await asyncio.sleep(0)
        task=asyncio.create_task(worker())
        with patch.dict(task_service._tasks,{},clear=True),patch.dict(instance_batch_service._runtime_tasks,{},clear=True),patch.dict(launch_task_service._tasks,{},clear=True),patch.object(launch_task_service,"_notification_tasks",{task}):
            report=await await_registered_workers(timeout_seconds=0.2)
        self.assertEqual(report["observed"],1)
        self.assertTrue(report["complete"])

if __name__=="__main__":
    unittest.main()
