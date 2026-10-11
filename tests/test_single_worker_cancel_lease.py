"""Cancellation of an OCI task must not prematurely clear its durable lease."""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
leases=importlib.import_module("app.oci_background_leases")

class DurableWorkerCancellation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.started=asyncio.Event()
        self.finish=asyncio.Event()
        self.events=[]
        def acquire(db,path):
            self.events.append(("acquire",path))
            return "lease-1"
        def release(db,token):
            self.events.append(("release",token))
        self.a=patch.object(leases,"acquire",side_effect=acquire)
        self.r=patch.object(leases,"release",side_effect=release)
        self.a.start()
        self.r.start()
    async def asyncTearDown(self):
        self.a.stop()
        self.r.stop()
        await asyncio.sleep(0)
        self.assertFalse(leases._detached_workers)

    async def test_cancelled_parent_keeps_worker_and_lease_until_complete(self):
        async def oci_work():
            self.started.set()
            await self.finish.wait()
            return 123
        outer=asyncio.create_task(leases.run_with_lease(None,kind="launch",task_id=7,coroutine=oci_work()))
        await self.started.wait()
        outer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await outer
        self.assertEqual(len(self.events),1)
        self.assertEqual(len(leases._detached_workers),1)
        self.finish.set()
        await asyncio.wait_for(asyncio.gather(*leases._detached_workers),1)
        await asyncio.sleep(0)
        self.assertEqual(self.events[-1],("release","lease-1"))

    async def test_inner_cancellation_retains_lease_fail_closed(self):
        async def cancellable_oci_work():
            self.started.set()
            await self.finish.wait()
        outer=asyncio.create_task(
            leases.run_with_lease(None,kind="launch",task_id=8,
                                  coroutine=cancellable_oci_work()))
        await self.started.wait()
        inner=next(iter(leases._detached_workers))
        # Unlike cancellation of the wrapper, cancellation of the inner
        # coroutine cannot prove that asyncio.to_thread work has stopped.
        inner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await outer
        await asyncio.sleep(0)
        self.assertEqual(self.events,[("acquire","background:launch:8")])
        self.assertFalse(leases._detached_workers)

    async def test_success_releases_lease(self):
        async def work(): return 42
        result=await leases.run_with_lease(None,kind="manual",task_id=2,coroutine=work())
        await asyncio.sleep(0)
        self.assertEqual(result,42)
        self.assertEqual(self.events[-1],("release","lease-1"))

    async def test_acquire_denied_closes_unscheduled_coroutine(self):
        async def work(): await asyncio.sleep(1)
        coroutine=work()
        with patch.object(leases,"acquire",side_effect=RuntimeError("maintenance")):
            with self.assertRaisesRegex(RuntimeError,"maintenance"):
                await leases.run_with_lease(None,kind="manual",task_id=4,coroutine=coroutine)
        self.assertIsNone(coroutine.cr_frame)

if __name__=="__main__": unittest.main()
