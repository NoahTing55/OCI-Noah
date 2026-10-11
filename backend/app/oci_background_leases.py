"""Durable OCI background-task lease for async workers.

The task's database row MUST be created atomically before it can be spawned.
Until then maintenance entry must reject nonterminal rows. A background task
claims its lease before executing OCI work and releases only on termination.
"""
from __future__ import annotations

from .oci_operation_leases import acquire, release


async def run_with_lease(db, *, kind: str, task_id: int, coroutine):
    token = None
    try:
        # Acquire synchronously before first await: there is no scheduling gap
        # between task startup and lease registration.
        token = acquire(db, f"background:{kind}:{int(task_id)}")
        return await coroutine
    finally:
        if token is None:
            # A rejected task cannot be left as an unawaited coroutine.
            coroutine.close()
        else:
            # Fail closed: a release failure preserves the durable lease.
            release(db, token)


async def await_registered_workers(*, timeout_seconds: float = 20.0) -> dict:
    """Best-effort graceful shutdown; never cancel an in-flight OCI call.

    Timeout is NOT evidence that workers have stopped. Persistent lease state
    remains authoritative for release maintenance admission.
    """
    import asyncio
    from . import task_service, instance_batch_service, launch_task_service
    pools = (
        tuple(task_service._tasks.values()),
        tuple(instance_batch_service._runtime_tasks.values()),
        tuple(launch_task_service._tasks.values()),
        tuple(launch_task_service._notification_tasks),
    )
    pending = {task for pool in pools for task in pool if not task.done()}
    if not pending:
        return {"observed": 0, "remaining": 0, "complete": True}
    done, still_running = await asyncio.wait(pending, timeout=timeout_seconds)
    # Critically do not call cancel(): asyncio.to_thread OCI requests may outlive
    # their coroutine and premature finally would clear its durable lease.
    return {"observed": len(pending), "remaining": len(still_running),
            "complete": not still_running}
