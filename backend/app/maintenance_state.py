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


class MaintenanceAdmissionBlocked(RuntimeError):
    """New OCI work is disallowed while an in-process maintenance operation is active."""


def reject_new_work_during_maintenance() -> None:
    with _lock:
        if _state["active"]:
            raise MaintenanceAdmissionBlocked(
                "系统正在维护，暂不接受新的 OCI 任务"
            )
