"""Read-only current-process background-work snapshot for release diagnostics.

This cannot prove all OCI operations are drained: other processes and
untracked asynchronous callbacks are outside this snapshot.
"""
from __future__ import annotations

from .oci_http_inflight import snapshot as http_snapshot


def snapshot() -> dict:
    from . import task_service, instance_batch_service, launch_task_service

    manual = sorted(int(k) for k, task in task_service._tasks.items() if not task.done())
    batches = sorted(int(k) for k, task in instance_batch_service._runtime_tasks.items() if not task.done())
    launch = sorted(int(k) for k, task in launch_task_service._tasks.items() if not task.done())
    notifications = sum(not task.done() for task in launch_task_service._notification_tasks)
    http = http_snapshot()
    from .oci_background_leases import detached_worker_snapshot
    workers = detached_worker_snapshot()
    return {
        "scope": "current_api_process_only",
        "manual_tasks": manual,
        "instance_batch_tasks": batches,
        "launch_tasks": launch,
        "launch_notification_tasks": notifications,
        "oci_http_writes": http["inflight_count"],
        "detached_oci_workers": workers["active"],
        "detached_workers_awaiting_cleanup": workers["done_pending_callback"],
        "observed_inflight": len(manual) + len(batches) + len(launch) + notifications + http["inflight_count"] + workers["active"],
        "may_double_count_workers": True,
        "release_authorized": False,
        "note": "Other processes, untracked threads and OCI calls may still be active.",
    }
