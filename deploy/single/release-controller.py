#!/usr/bin/env python3
"""OCI-N&T single-container cutover controller: read-only fail-closed PLAN ONLY.

A complete release coordinator needs authenticated atomic maintenance admission,
in-flight attestation, explicitly approved outage, controlled image switch,
and tested rollback. No production cutover is allowed until those are built.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
IMAGE_PREFIX = "ghcr.io/noahting55/oci-noah-single:sha-"
SERVICE_NAMES = ("oci-nt-api", "oci-nt-web", "oci-nt-monitor-agent", "oci-nt-docker-guard")


def call(args):
    p = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=30, check=False)
    if p.returncode != 0:
        raise RuntimeError("probe failed: " + args[0] + " " + args[1])
    return p.stdout


def inspect(sha: str, *, runner=call, root=ROOT) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("full lowercase 40-char image SHA required")
    db = root / "data/oci-nt.db"
    if db.is_symlink() or not db.is_file():
        raise RuntimeError("expected production DB missing or symlink")
    if not (root / ".env").is_file():
        raise RuntimeError("missing current .env")
    if not (root / "docker-compose.yml").is_file():
        raise RuntimeError("missing production Compose")
    with sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=10) as conn:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        v = conn.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()
        if not v or int(v[0]) != 7:
            raise RuntimeError("unsupported database schema")
        entries = {k:conn.execute("SELECT setting_value FROM system_settings WHERE setting_key=?", (k,)).fetchone()
                   for k in ("oci_nt_release_maintenance_v1", "oci_nt_http_operation_leases_v1")}
        # Do not interpret empty rows as global OCI quiescence.
        import json as _json
        maintenance = _json.loads(entries["oci_nt_release_maintenance_v1"][0]) if entries["oci_nt_release_maintenance_v1"] else {"active":False}
        leases = _json.loads(entries["oci_nt_http_operation_leases_v1"][0]) if entries["oci_nt_http_operation_leases_v1"] else {}
        if not isinstance(maintenance, dict) or type(maintenance.get("active")) is not bool:
            raise RuntimeError("invalid maintenance record")
        if not isinstance(leases, dict):
            raise RuntimeError("invalid OCI leases")
        terminal = {"COMPLETED","PARTIAL","FAILED","CANCELLED","INTERRUPTED","SUCCESS","FINISHED","STOPPED"}
        pending = {}
        for table in ("manual_tasks","launch_jobs"):
            pending[table] = sum(n for status,n in conn.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status")
                                 if str(status or "NULL").upper() not in terminal)
    target = IMAGE_PREFIX + sha
    info = json.loads(runner(["docker","image","inspect",target]))[0]
    if info.get("Architecture") != "amd64":
        raise RuntimeError("single image architecture mismatch")
    original = {}
    for name in SERVICE_NAMES:
        state = json.loads(runner(["docker","inspect",name]))[0]
        if state.get("Config",{}).get("Labels",{}).get("com.docker.compose.project") != "oci-nt":
            raise RuntimeError("unexpected ownership for " + name)
        if state.get("State",{}).get("Health",{}).get("Status") != "healthy":
            raise RuntimeError("service not healthy: " + name)
        original[name] = state["Config"]["Image"]
    socket = Path("/var/run/docker.sock")
    if not socket.is_socket():
        raise RuntimeError("Docker socket missing")
    socket_gid = socket.stat().st_gid
    if socket_gid <= 0:
        raise RuntimeError("Docker socket GID must not be root")
    return {
        "mode": "PLAN_ONLY",
        "single_image": target,
        "single_image_id": info["Id"],
        "original_images": original,
        "schema": int(v[0]),
        "recorded_oci_leases": len(leases),
        "nonterminal_task_counts": pending,
        "maintenance_active": maintenance["active"],
        "docker_socket_gid": socket_gid,
        "checks_passed": True,
        "recorded_work_drained": (not any(pending.values()) and not leases),
        "recorded_work_gate": "RECORDED_IDLE" if not any(pending.values()) and not leases else "BLOCKED_ACTIVE_RECORDED_WORK",
        "execution_supported": False,
        "release_authorized": False,
        "blockers": [
            "No atomic release admission handshake with all HTTP/detached OCI paths",
            "No independent current live OCI in-flight attestation",
            "No production-proven image+database rollback procedure",
            "Single-container root/Docker Socket threat boundary not approved",
        ]
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,
                          "reason":"Production execution intentionally unavailable until all safety blockers are addressed"}))
        return 4
    try:
        report = inspect(args.sha)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError, TypeError, IndexError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,"reason":type(exc).__name__}))
        return 3
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
