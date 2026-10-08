"""Per-process in-flight OCI HTTP write-request counter (observability only).

This covers only protected HTTP write requests, not background OCI tasks,
threads that outlive request handling, or other API/worker processes.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

_lock = threading.RLock()
_active: dict[int, dict] = {}
_next_id = 0


@contextmanager
def track_oci_http_write(path: str) -> Iterator[None]:
    global _next_id
    with _lock:
        _next_id += 1
        token = _next_id
        _active[token] = {
            "path": str(path).split("?", 1)[0],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
    try:
        yield
    finally:
        with _lock:
            _active.pop(token, None)


def snapshot() -> dict:
    with _lock:
        return {
            "process_scope": "current_api_process_only",
            "inflight_count": len(_active),
            "inflight": [dict(entry) for entry in _active.values()],
            "release_authorized": False,
        }
