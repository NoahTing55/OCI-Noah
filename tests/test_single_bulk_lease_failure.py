import asyncio
import os
from unittest.mock import patch
import unittest

class BulkLeaseFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_lease_refusal_finalizes_persisted_job(self):
        # CI can carry empty integer env vars from previous tests; keep this
        # isolated import deterministic without modifying application defaults.
        with patch.dict(os.environ, {
            "SECRET_KEY": "test-only-secret",
            "CREDENTIAL_ENCRYPTION_KEY": "pX6YVfFGhm8Cg1V0scP78CvWVDsj2-4WsC7C3LmD8ZE=",
            "ADMIN_PASSWORD": "test-only-password",
            "ACCESS_TOKEN_MINUTES": "1440",
            "BULK_CHECK_INTERVAL_SECONDS": "30",
        }):
            from app import bulk_check_service as service
        finished=[]
        async def refuse(*args,coroutine,**kwargs):
            coroutine.close()
            raise RuntimeError("maintenance active")
        with patch.object(service,"run_with_lease",side_effect=refuse), \
             patch.object(service,"finish_job",side_effect=lambda *a,**kw: finished.append((a,kw))):
            with self.assertRaisesRegex(RuntimeError,"maintenance active"):
                await service._run_bulk_check_with_lease(
                    job_id=123,accounts=[],requested_by="test",
                    ip_address=None,interval_seconds=0)
        self.assertEqual(len(finished),1)
        self.assertEqual(finished[0][1]["status"],"FAILED")
        self.assertNotIn("maintenance active",finished[0][1]["error"])

    async def test_outer_cancellation_does_not_release_inflight_worker_lease(self):
        from app import oci_background_leases as leases
        entered=asyncio.Event()
        finish=asyncio.Event()
        recorded=[]
        async def worker():
            entered.set()
            await finish.wait()
            return "done"
        with patch.object(leases,"acquire",return_value="lease-123"), \
             patch.object(leases,"release",side_effect=lambda *args:recorded.append(args)):
            outer=asyncio.create_task(leases.run_with_lease("test",kind="bulk-check",task_id=1,coroutine=worker()))
            await asyncio.wait_for(entered.wait(),1)
            outer.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await outer
            self.assertEqual(recorded,[])
            self.assertGreaterEqual(leases.detached_worker_snapshot()["active"],1)
            finish.set()
            for _ in range(30):
                if recorded:break
                await asyncio.sleep(0)
            self.assertEqual(recorded,[("test","lease-123")])
if __name__=="__main__":unittest.main()
