#!/usr/bin/env python3
"""Static release admission coverage audit, independent of production state.

This is a regression detector, not a proof of all OCI egress or runtime drain.
Untracked work requires manual review and keeps production cutover blocked.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import re

ROOT = Path(__file__).resolve().parents[2]
TRACKED = {
    "task_service.py": ("run_with_lease(settings.db_path", "asyncio.create_task("),
    "instance_batch_service.py": ("run_with_lease(settings.db_path", "asyncio.create_task("),
    "launch_task_service.py": ("run_with_lease(\n            settings.db_path", "asyncio.create_task("),
}
BASE = ROOT / "backend/app"

def audit(base: Path = BASE) -> dict:
    checked = {}
    for name,(lease,spawn) in TRACKED.items():
        src = (base/name).read_text(encoding="utf-8")
        checked[name] = {"lease_wrapper_present": lease in src,
                         "spawn_present": spawn in src}
    http = (base/"main.py").read_text(encoding="utf-8")
    checked["main.py"] = {
        "atomic_http_lease_present": "acquire_oci_lease(settings.db_path" in http,
        "release_present": "release_oci_lease(settings.db_path" in http,
    }
    runtime = (base/"runtime_drain_snapshot.py").read_text(encoding="utf-8")
    checked["runtime_drain_snapshot.py"] = {
        "explicitly_process_local": "current_api_process_only" in runtime,
        "never_authorizes_release": '"release_authorized": False' in runtime,
    }
    # Discovery is deliberately broader than the three known task pools.
    # Each call site is reported for human review. A static occurrence of
    # run_with_lease is NOT proof that every spawned task is protected.
    patterns = {
        "asyncio_create_task": re.compile(r"\\b(?:asyncio\\.)?create_task\\s*\\("),
        "asyncio_to_thread": re.compile(r"\\basyncio\\.to_thread\\s*\\("),
        "run_in_executor": re.compile(r"\\brun_in_executor\\s*\\("),
        "threading_thread": re.compile(r"\\bthreading\\.Thread\\s*\\("),
    }
    spawn_inventory = []
    for source in sorted(base.rglob("*.py")):
        if source.is_symlink() or not source.is_file():
            raise OSError("unexpected source link")
        data = source.read_text(encoding="utf-8")
        for lineno, line in enumerate(data.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for kind, pattern in patterns.items():
                if pattern.search(line):
                    spawn_inventory.append({
                        "file": str(source.relative_to(base)),
                        "line": lineno,
                        "kind": kind,
                        "requires_manual_oci_egress_review": True,
                    })
    checked["spawn_inventory"] = {"scan_completed": True}
    gaps = [
        "Static source checks do not prove every asynchronous OCI operation is registered",
        "Launch-success notifications and detached callbacks require separate lifecycle review",
        "Independent monitor/guard and cross-process operations have no complete runtime attestation",
        "Current-process snapshots are not a global stop-and-drain protocol",
    ]
    passed = all(all(states.values()) for states in checked.values())
    return {"status":"STATIC_CONTRACT_OK" if passed else "STATIC_CONTRACT_FAILED",
            "checks":checked,"spawn_inventory":spawn_inventory,
            "unverified_runtime_paths":gaps,
            "release_authorized":False,"production_cutover_allowed":False}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=BASE)
    args=parser.parse_args()
    try:
        result=audit(args.source)
    except (OSError, UnicodeError) as exc:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,"reason":type(exc).__name__}))
        return 3
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0 if result["status"]=="STATIC_CONTRACT_OK" else 3

if __name__=="__main__":
    sys.exit(main())
