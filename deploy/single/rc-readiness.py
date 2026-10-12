#!/usr/bin/env python3
"""OCI-N&T 2.0 single-container RC evidence gate (report-only).

Does not execute Docker, OCI or SQLite operations. A trusted operator supplies
independently verified evidence; self-reported JSON alone is not authorization.
The gate remains unarmed until a separately reviewed production executor exists.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

REQUIRED = (
    "stable_ui_parity",
    "api_routes_parity",
    "tenant_and_proxy_parity",
    "oci_mutation_inventory_complete",
    "all_writers_gate_enforced",
    "detached_tasks_drained",
    "live_oci_leases_drained",
    "cross_process_maintenance_persistent",
    "four_services_stopped",
    "stopped_sqlite_backup_verified",
    "schema7_rollback_compatible",
    "credentials_restored_in_isolated_test",
    "old_pinned_images_restore_verified",
    "single_service_health_verified",
    "docker_socket_threat_review_approved",
    "production_scope_isolated",
)
BLOCKED_SCOPE = ("/opt/oci--nt",)
ALLOWED_SCOPE = "/opt/oci-nt"

def assess(record):
    if not isinstance(record, dict) or set(record) != {"scope","checks","manifest_version"}:
        raise ValueError("invalid RC evidence format")
    if record["manifest_version"] != 1 or type(record["manifest_version"]) is not int:
        raise ValueError("unsupported manifest")
    if record["scope"] != ALLOWED_SCOPE or any(record["scope"].startswith(s) for s in BLOCKED_SCOPE):
        raise ValueError("unexpected project scope")
    checks = record["checks"]
    if not isinstance(checks, dict) or set(checks) != set(REQUIRED):
        raise ValueError("missing/unknown acceptance checks")
    # Values must be actual booleans: never accept 1 or nonempty strings.
    if any(type(v) is not bool for v in checks.values()):
        raise ValueError("nonboolean acceptance evidence")
    failed = [name for name in REQUIRED if checks[name] is not True]
    return {
        "status": "RC_EVIDENCE_INCOMPLETE" if failed else "RC_REPORTED_EVIDENCE_COMPLETE_NOT_AUTHORIZED",
        "missing_proofs": failed,
        "single_container_production_authorized": False,
        "cutover_apply_allowed": False,
        "production_executor_integrated": False,
        "scope": ALLOWED_SCOPE,
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--evidence",type=Path)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if args.apply:
        print(json.dumps({"status":"BLOCKED","cutover_apply_allowed":False}))
        return 4
    if args.evidence is None:
        print(json.dumps({"status":"MISSING_EVIDENCE","cutover_apply_allowed":False}))
        return 3
    try:
        evidence=json.loads(args.evidence.read_text(encoding="utf-8"))
        result=assess(evidence)
    except (ValueError,TypeError,OSError,json.JSONDecodeError):
        print(json.dumps({"status":"INVALID_EVIDENCE","cutover_apply_allowed":False}))
        return 3
    print(json.dumps(result,indent=2))
    return 3 if result["missing_proofs"] else 0

if __name__=="__main__":
    raise SystemExit(main())
