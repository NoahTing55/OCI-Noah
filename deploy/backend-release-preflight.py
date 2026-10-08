#!/usr/bin/env python3
"""Unified fail-closed read-only backend release preflight.

All subprocesses are fixed known local tools. An assessment never authorizes
a backend release, even if all available evidence checks succeed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKS = (
    ("database", "backend-preflight.py", ("--db", "{db}")),
    ("drain", "release-drain-check.py", ("--db", "{db}")),
    ("services", "backend-release-evidence.py", ("--db", "{db}")),
    ("candidate", "backend-candidate-check.py", ("--sha", "{sha}")),
)


def assess(db: Path, sha: str, runner=subprocess.run) -> dict:
    checks = {}
    all_passed = True
    for name, script, args in CHECKS:
        resolved = [str(db) if part == "{db}" else sha if part == "{sha}" else part for part in args]
        command = [sys.executable, str(HERE / script), *resolved]
        try:
            result = runner(command, capture_output=True, text=True, timeout=45, check=False)
            try:
                payload = json.loads(result.stdout)
            except (ValueError, TypeError):
                payload = {"error": "Checker did not return JSON"}
            ok = result.returncode == 0 and isinstance(payload, dict) and "error" not in payload
            checks[name] = {"passed": ok, "exit_code": result.returncode, "report": payload}
            all_passed = all_passed and ok
        except (OSError, subprocess.SubprocessError) as exc:
            all_passed = False
            checks[name] = {"passed": False, "error": type(exc).__name__}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "observed_checks_passed": all_passed,
        "release_authorized": False,
        "blocking_requirements": [
            "cross-process OCI request and background task drain has not been proven",
            "candidate API database migration and rollback compatibility is not verified",
            "authenticated operator release approval workflow is not implemented",
        ],
        "status": "EVIDENCE_ONLY" if all_passed else "BLOCKED",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    result = assess(args.db, args.sha)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["observed_checks_passed"] else 3


if __name__ == "__main__":
    sys.exit(main())
