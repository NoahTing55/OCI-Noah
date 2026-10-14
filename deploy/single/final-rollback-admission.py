#!/usr/bin/env python3
"""Report-only final rollback admission check. NEVER authorizes live cutover.

Evidence is externally obtained and not trusted as runtime attestation. All
required inputs must be present and internally consistent. No Docker or writes.
"""
import argparse
import json
import re
from pathlib import Path

OLD=("oci-nt-api","oci-nt-web","oci-nt-monitor-agent","oci-nt-docker-guard")
DIGEST=re.compile(r"sha256:[0-9a-f]{64}\Z")
CHECKS=("ingress_frozen","operator_maintenance_window_approved",
        "old_oci_processes_exited","final_snapshot_hash_verified",
        "final_snapshot_integrity_ok","final_snapshot_schema7",
        "final_snapshot_accounts_match","rollback_compose_verified",
        "rollback_rehearsal_passed")

def inspect(e):
    if not isinstance(e,dict): raise ValueError("evidence must be object")
    images=e.get("original_image_ids")
    if not isinstance(images,dict) or set(images)!=set(OLD):
        raise ValueError("four original image ids required")
    image_ok=all(isinstance(x,str) and DIGEST.fullmatch(x) for x in images.values())
    checks={k:e.get(k) is True for k in CHECKS}
    checks["original_image_ids_pinned"]=bool(image_ok)
    checks["snapshot_hash_present"]=isinstance(e.get("final_snapshot_sha256"),str) and bool(re.fullmatch(r"[0-9a-f]{64}",e["final_snapshot_sha256"]))
    checks["candidate_stopped_for_rollback"]=e.get("candidate_stopped_for_rollback") is True
    # CI proof and operator claims do not prove all OCI SDK calls were drained.
    return {"status":"ROLLBACK_EVIDENCE_COMPLETE_PLAN_ONLY" if all(checks.values()) else "BLOCKED_ROLLBACK_EVIDENCE",
            "checks":checks,"missing":[k for k,v in checks.items() if not v],
            "sdk_global_quiescence_proven":False,
            "live_restore_verified":False,
            "production_cutover_authorized":False}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--evidence-json",type=Path,required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if args.apply:
        print('{"status":"BLOCKED","production_cutover_authorized":false}')
        return 4
    try:
        result=inspect(json.loads(args.evidence_json.read_text()))
    except (OSError,ValueError,TypeError):
        print('{"status":"INVALID_EVIDENCE","production_cutover_authorized":false}')
        return 3
    print(json.dumps(result,indent=2))
    return 0 if not result["missing"] else 3

if __name__=="__main__":raise SystemExit(main())
