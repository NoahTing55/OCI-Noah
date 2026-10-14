"""Behavioral tests for OCI background lease lifetime; synthetic coroutines only."""
import asyncio
import unittest
from unittest.mock import patch

from app import oci_background_leases as mod


class WorkerLeaseLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertFalse(mod._detached_workers)
        self.releases=[]
        self.acquires=[]
        self.p_acq=patch.object(mod,"acquire",side_effect=lambda db,path:self.acquires.append(path) or "test-token")
        self.p_rel=patch.object(mod,"release",side_effect=lambda db,token:self.releases.append(token))
        self.p_acq.start()
        self.p_rel.start()
        self.addCleanup(self.p_acq.stop)
        self.addCleanup(self.p_rel.stop)

    async def drain_callbacks(self):
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_success_releases_once_after_worker_finishes(self):
        async def completed(): return "ok"
        result=await mod.run_with_lease("synthetic-db",kind="test",task_id=1,coroutine=completed())
        await self.drain_callbacks()
        self.assertEqual(result,"ok")
        self.assertEqual(self.acquires,["background:test:1"])
        self.assertEqual(self.releases,["test-token"])
        self.assertFalse(mod._detached_workers)

    async def test_outer_cancel_keeps_lease_until_worker_ends(self):
        started=asyncio.Event()
        release_worker=asyncio.Event()
        async def worker():
            started.set()
            await release_worker.wait()
            return "finished"
        parent=asyncio.create_task(mod.run_with_lease("synthetic-db",kind="test",task_id=2,coroutine=worker()))
        await started.wait()
        parent.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await parent
        await self.drain_callbacks()
        self.assertEqual(self.releases,[])
        self.assertTrue(mod._detached_workers)
        release_worker.set()
        await asyncio.wait_for(asyncio.gather(*tuple(mod._detached_workers)),timeout=3)
        await self.drain_callbacks()
        self.assertEqual(self.releases,["test-token"])
        self.assertFalse(mod._detached_workers)

    async def test_cancelled_inner_worker_leaves_lease_for_investigation(self):
        started=asyncio.Event()
        async def worker():
            started.set()
            await asyncio.Event().wait()
        parent=asyncio.create_task(mod.run_with_lease("synthetic-db",kind="test",task_id=3,coroutine=worker()))
        await started.wait()
        (child,)=tuple(mod._detached_workers)
        child.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await parent
        await self.drain_callbacks()
        self.assertEqual(self.releases,[])
        self.assertFalse(mod._detached_workers)


if __name__=="__main__":
    unittest.main()
