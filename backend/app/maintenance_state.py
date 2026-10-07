from __future__ import annotations

import threading
from datetime import datetime, timezone

_lock = threading.RLock()
_state = {
    "active": False,
    "operation": None,
    "started_at": None,
    "detail": None,
}


def begin(operation: str, detail: str | None = None) -> bool:
    with _lock:
        if _state["active"]:
            return False
        _state.update({
            "active": True,
            "operation": str(operation),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "detail": detail,
        })
        return True


def end() -> None:
    with _lock:
        _state.update({"active": False, "operation": None, "started_at": None, "detail": None})


def status() -> dict:
    with _lock:
        return dict(_state)
