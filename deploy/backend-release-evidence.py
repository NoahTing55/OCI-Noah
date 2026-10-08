#!/usr/bin/env python3
"""Read-only OCI-N&T backend release evidence; never issues deployment approval.

Checks database task drain and all four expected Docker containers, without
calling any Docker mutation command or touching the production database.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

CONTAINERS = ("oci-nt-api", "oci-nt-web", "oci-nt-monitor-agent", "oci-nt-docker-guard")


def load_drain():
    path = Path(__file__).with_name("release-drain-check.py")
    spec = importlib.util.spec_from_file_location("release_drain_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def container_evidence(runner=subprocess.run) -> dict:
    result = {}
    for name in CONTAINERS:
        p = runner(
            ["docker", "inspect", "--format",
             "{{json .State}}", name],
            capture_output=True, text=True, timeout=12, check=True,
        )
        state = json.loads(p.stdout)
        result[name] = {
            "running": state.get("Running") is True,
            "health": (state.get("Health") or {}).get("Status", "unreported"),
        }
    return result


def collect(db: Path, runner=subprocess.run) -> dict:
    drain = load_drain().report(db)
    containers = container_evidence(runner)
    healthy = all(
        item["running"] and item["health"] == "healthy"
        for item in containers.values()
    )
    return {
        "database": drain,
        "containers": containers,
        "observed_services_healthy": healthy,
        "evidence_complete": bool(drain["db_task_drain_ready"] and healthy),
        "release_authorized": False,
        "unverified": [
            "in-flight OCI requests across all API processes",
            "detached background worker tasks",
            "migration compatibility of the candidate API image",
            "operator approval and rollback readiness",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = collect(args.db)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "BLOCKED", "release_authorized": False,
                          "error": str(exc)}, ensure_ascii=False))
        return 2
    result["status"] = "EVIDENCE_COLLECTED" if result["evidence_complete"] else "BLOCKED"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["evidence_complete"] else 3


if __name__ == "__main__":
    sys.exit(main())
