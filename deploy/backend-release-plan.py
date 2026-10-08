#!/usr/bin/env python3
"""Conservative backend release plan generator. NO deploy, restart, lock, or DB write.

Explicitly blocks execution until unimplemented cross-process release safety
requirements are independently established.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

CHECKS = (
    ("task_preflight", "backend-preflight.py", ("--db", "{db}")),
    ("release_evidence", "backend-release-evidence.py", ("--db", "{db}")),
    ("candidate", "backend-candidate-check.py", ("--sha", "{sha}")),
    ("process_topology", "backend-process-topology.py", ()),
)

STEPS = (
    ("1", "Acquire authenticated, durable cross-process maintenance admission lock", False),
    ("2", "Block all OCI SDK entry paths, scheduled jobs and detached workers atomically", False),
    ("3", "Wait for all existing OCI work across all processes to complete", False),
    ("4", "Validate DB drain, HTTP inflight and API/monitor/guard process health", False),
    ("5", "Create and independently verify a WAL-safe SQLite release backup", True),
    ("6", "Confirm image provenance, database migration compatibility and rollback strategy", False),
    ("7", "Obtain explicit human approval for precisely pinned API image", False),
    ("8", "Apply only explicitly approved backend service and verify, then release lock", False),
)

def plan(db: Path, sha: str, runner=subprocess.run) -> dict:
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("Use a complete lowercase 40-character Git SHA")
    here = Path(__file__).resolve().parent
    results = {}
    for name, script, template in CHECKS:
        args = [str(db) if x == "{db}" else sha if x == "{sha}" else x for x in template]
        command = [sys.executable, str(here / script), *args]
        try:
            out = runner(command, text=True, capture_output=True, timeout=60, check=False)
            payload = json.loads(out.stdout)
            ok = out.returncode == 0 and isinstance(payload, dict)
            results[name] = {"ok": ok, "exit_code": out.returncode, "report": payload}
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            results[name] = {"ok": False, "error": type(exc).__name__}
    return {
        "mode": "PLAN_ONLY",
        "target_sha": sha,
        "observed_checks_passed": all(item["ok"] for item in results.values()),
        "check_results": results,
        "release_steps": [
            {"step": n, "description": label, "implemented": ready}
            for n, label, ready in STEPS
        ],
        "unimplemented_blockers": [label for _, label, ready in STEPS if not ready],
        "release_authorized": False,
        "execution_supported": False,
    }

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    try:
        result = plan(args.db, args.sha)
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "release_authorized": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["observed_checks_passed"] else 3

if __name__ == "__main__":
    sys.exit(main())
