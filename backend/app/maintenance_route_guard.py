"""Conservative HTTP admission gate for release-maintenance mode.

This protects HTTP OCI write entry points. It is *not* an atomic task admission
barrier and cannot stop work already inside a request or started by schedulers.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

KEY = "oci_nt_release_maintenance_v1"
METHODS = frozenset(("POST", "PUT", "PATCH", "DELETE"))


def protected_oci_write(method: str, path: str) -> bool:
    if method.upper() not in METHODS or not path.startswith("/api/v1/"):
        return False
    subpath = path[len("/api/v1/"):]
    # Cancellation remains available to drain in-flight work safely.
    if subpath.endswith("/cancel"):
        return False
    # Account creation/import validates OCI credentials and can make SDK calls.
    if subpath == "accounts":
        return True
    # Protection intentionally errs on the side of blocking OCI mutations;
    # authentication, backups and observability remain available.
    return (
        subpath.startswith(("accounts/", "instances/", "bulk/", "tasks/", "launch/"))
        or subpath.startswith(("proxy/", "proxy-allocation/"))
        or subpath.startswith("settings/account-check-schedule/")
    )


def maintenance_active(db: Path) -> bool:
    if not db.is_file():
        raise RuntimeError("Production database missing: refusing OCI mutation")
    with sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=10) as con:
        con.execute("PRAGMA query_only=ON")
        row = con.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = ?",
            (KEY,),
        ).fetchone()
    if row is None:
        return False
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Maintenance marker malformed") from exc
    if not isinstance(payload, dict) or type(payload.get("active")) is not bool:
        raise RuntimeError("Maintenance marker malformed")
    return payload["active"]


def protected_oci_request(method: str, path: str) -> bool:
    """Conservatively lease OCI-facing reads as well as existing mutations.

    Some GET endpoints can perform live OCI SDK calls. A read can therefore
    outlive release maintenance just like a write. Do not block health, login,
    cancellation or local backup/release observability endpoints.
    """
    if protected_oci_write(method, path):
        return True
    if method.upper() != "GET" or not path.startswith("/api/v1/"):
        return False
    subpath = path[len("/api/v1/"):]
    # Safe-resume GET preview performs live OCI instance queries.
    if subpath.startswith("tasks/") and subpath.endswith("/safe-resume-preview"):
        return True
    # Legacy account-check GET is generally cached, but OCI-facing endpoints
    # require fail-closed admission during release maintenance.
    if subpath == "instances" or subpath.startswith("instances/"):
        return True
    if subpath.startswith("accounts/") and (
        subpath.endswith("/instances")
        or "/instances/" in subpath
    ):
        return True
    return subpath.startswith(("resources/", "launch/resources/", "oci/"))
