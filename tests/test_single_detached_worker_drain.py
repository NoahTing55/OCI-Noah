"""Test detached OCI workers are visible and cancellation retains durable leases."""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))

from app import oci_background_leases as workers

class DetachedWorkers(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        for task in tuple(workers._detached_workers):
            if not task.done():
                task.cancel()
        await asyncio.sleep(0)

    async def test_cancelled_request_retains_lease_until_inner_finishes(self):
        started=asyncio.Event()
        release_inner=asyncio.Event()
        async def operation():
            started.set()
            await release_inner.wait()
            return "done"
        with patch.object(workers,"acquire",return_value="t") as acquire, patch.object(workers,"release") as release:
            request=asyncio.create_task(workers.run_with_lease(Path("/unused"),
                kind="test",task_id=1,coroutine=operation()))
            await started.wait()
            self.assertEqual(workers.detached_worker_snapshot()["active"],1)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertEqual(workers.detached_worker_snapshot()["active"],1)
            release.assert_not_called()
            release_inner.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            release.assert_called_once_with(Path("/unused"),"t")
            self.assertEqual(workers.detached_worker_snapshot()["active"],0)
            self.assertFalse(workers.detached_worker_snapshot()["release_authorized"])

if __name__=="__main__":
    unittest.main()
