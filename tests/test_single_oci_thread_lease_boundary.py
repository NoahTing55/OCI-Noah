"""Synthetic blocking threads prove durable OCI lease cancellation boundaries."""
import asyncio
import threading
import unittest
from unittest.mock import patch

from app import oci_background_leases as leases


class ThreadLeaseBoundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertFalse(leases._detached_workers)
        self.acquired=[]
        self.released=[]
        self.acquire_patch=patch.object(leases,"acquire",side_effect=lambda db,path:self.acquired.append(path) or "synthetic-token")
        self.release_patch=patch.object(leases,"release",side_effect=lambda db,token:self.released.append(token))
        self.acquire_patch.start()
        self.release_patch.start()
        self.addCleanup(self.acquire_patch.stop)
        self.addCleanup(self.release_patch.stop)

    async def flush(self):
        for _ in range(8):
            await asyncio.sleep(0)

    async def test_outer_cancellation_does_not_release_while_thread_is_blocked(self):
        started=threading.Event()
        resume=threading.Event()
        exited=threading.Event()
        def blocking_sdk_simulator():
            started.set()
            try:
                if not resume.wait(timeout=8):
                    raise TimeoutError("synthetic worker not released")
                return "finished"
            finally:
                exited.set()
        async def worker():
            return await asyncio.to_thread(blocking_sdk_simulator)
        parent=asyncio.create_task(leases.run_with_lease(
            "fake-db",kind="thread",task_id=100,coroutine=worker()))
        try:
            ready=await asyncio.to_thread(started.wait,3)
            self.assertTrue(ready,"synthetic thread did not start")
            parent.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await parent
            await self.flush()
            self.assertFalse(exited.is_set())
            self.assertEqual(self.released,[])
            self.assertEqual(self.acquired,["background:thread:100"])
            self.assertTrue(leases._detached_workers)
        finally:
            resume.set()
        await asyncio.wait_for(asyncio.gather(*tuple(leases._detached_workers)),timeout=5)
        await self.flush()
        self.assertTrue(exited.is_set())
        self.assertEqual(self.released,["synthetic-token"])
        self.assertFalse(leases._detached_workers)

    async def test_inner_cancellation_keeps_lease_while_thread_continues(self):
        started=threading.Event()
        resume=threading.Event()
        exited=threading.Event()
        def blocking_sdk_simulator():
            started.set()
            try:
                resume.wait(timeout=8)
            finally:
                exited.set()
        async def worker():
            await asyncio.to_thread(blocking_sdk_simulator)
        parent=asyncio.create_task(leases.run_with_lease(
            "fake-db",kind="thread",task_id=101,coroutine=worker()))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,3))
            (child,)=tuple(leases._detached_workers)
            child.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await parent
            await self.flush()
            self.assertFalse(exited.is_set())
            self.assertEqual(self.released,[])
        finally:
            resume.set()
        self.assertTrue(await asyncio.to_thread(exited.wait,3))
        await self.flush()
        # The coroutine was cancelled, so the runtime cannot safely establish
        # that the SDK call completed; durable lease remains for investigation.
        self.assertEqual(self.released,[])
        self.assertFalse(leases._detached_workers)


if __name__=="__main__":
    unittest.main()
