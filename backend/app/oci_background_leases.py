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
