#!/usr/bin/env python3
"""Read-only production process topology inspection for OCI-N&T release planning.

Never grants backend deployment authorization. Inspects the Docker states and
process lists, not only the API container's HTTP counter.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

NAMES = ("oci-nt-api", "oci-nt-monitor-agent", "oci-nt-docker-guard")


def audit(runner=subprocess.run) -> dict:
    processes = {}
    for name in NAMES:
        command = ["docker", "top", name, "-eo", "pid,ppid,args"]
        result = runner(command, capture_output=True, text=True, timeout=15, check=True)
        rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(rows) < 2 or "PID" not in rows[0].upper():
            raise RuntimeError(f"Cannot verify running process list: {name}")
        # Do not expose process arguments: they can contain secret values.
        processes[name] = {"reported_process_rows": len(rows) - 1}
    return {
        "processes": processes,
        "topology_observed": True,
        "cross_process_quiescence_verified": False,
        "release_authorized": False,
        "status": "INSPECTED_ONLY",
        "limitations": [
            "Process lists cannot prove absence of OCI SDK requests",
            "In-flight and queued operations may exist in threads/background tasks",
            "Process snapshot is not atomic with new task admission",
        ],
    }


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        result = audit()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "BLOCKED", "release_authorized": False,
                          "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
