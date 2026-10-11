"""Durable OCI worker leases with cancellation-safe ownership.

An asyncio cancellation must not release a durable OCI lease while a shielded
worker (including an asyncio.to_thread OCI SDK request) is still running.
"""
from __future__ import annotations

import asyncio
import logging

from .oci_operation_leases import acquire, release

log = logging.getLogger(__name__)
# Strong references: cancellation of the request-facing task must never
# garbage-collect the underlying in-flight OCI coroutine.
_detached_workers: set[asyncio.Task] = set()


async def run_with_lease(db, *, kind: str, task_id: int, coroutine):
    try:
        # Lease acquisition precedes scheduling the worker's first instruction.
        token = acquire(db, f"background:{kind}:{int(task_id)}")
    except BaseException:
        coroutine.close()
        raise

    try:
        worker = asyncio.create_task(coroutine)
    except BaseException:
        release(db, token)
        coroutine.close()
        raise

    def finish(done: asyncio.Task) -> None:
        _detached_workers.discard(done)
        # A cancelled inner coroutine may still have a running thread spawned
        # by asyncio.to_thread(). A Task reporting done/cancelled is NOT proof
        # that the underlying synchronous OCI SDK call returned. Keep its
        # durable lease for explicit operator investigation instead of
        # falsely attesting that all OCI traffic has drained.
        if done.cancelled():
            log.error(
                "OCI worker cancelled before verified completion; "
                "durable lease retained for investigation: %s", token
            )
            return
        # Non-cancelled completion means the awaited coroutine ran to
        # completion (including its awaited to_thread() calls).
        try:
            release(db, token)
        except BaseException:
            log.exception("OCI durable worker lease release failed; retained fail-closed")
        try:
            done.exception()
        except BaseException:
            log.exception("Unable to inspect completed OCI worker")

    _detached_workers.add(worker)
    worker.add_done_callback(finish)
    # Shield prevents cancellation of the outer task from propagating into
    # the coroutine and releasing its durable lease before to_thread ends.
    return await asyncio.shield(worker)


def detached_worker_snapshot() -> dict:
    """Diagnostic-only snapshot, not an OCI shutdown or admission attestation."""
    workers = tuple(_detached_workers)
    return {
        "active": sum(not task.done() for task in workers),
        "done_pending_callback": sum(task.done() for task in workers),
        "scope": "current_api_process_only",
        "release_authorized": False,
    }


async def await_registered_workers(*, timeout_seconds: float = 20.0) -> dict:
    """Wait for all known workers without cancelling OCI SDK operations.

    Timeout is NOT proof of drain; unresolved SQLite leases remain authoritative.
    """
    from . import task_service, instance_batch_service, launch_task_service
    pools = (
        tuple(task_service._tasks.values()),
        tuple(instance_batch_service._runtime_tasks.values()),
        tuple(launch_task_service._tasks.values()),
        tuple(launch_task_service._notification_tasks),
        tuple(_detached_workers),
    )
    pending = {task for pool in pools for task in pool if not task.done()}
    if not pending:
        return {"observed": 0, "remaining": 0, "complete": True}
    _done, still_running = await asyncio.wait(pending, timeout=timeout_seconds)
    return {"observed": len(pending), "remaining": len(still_running),
            "complete": not still_running}
