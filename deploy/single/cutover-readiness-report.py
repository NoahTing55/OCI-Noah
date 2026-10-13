#!/usr/bin/env python3
"""Report-only OCI-N&T cutover readiness assessment. Never performs a cutover.

Accepts only non-secret JSON from the read-only production audit. An apparent
idle database never establishes process-wide SDK quiescence.
"""
import argparse
import json
from pathlib import Path

def assess(audit):
    if not isinstance(audit,dict):
        raise ValueError("audit must be an object")
    db=audit.get("database")
    backup=audit.get("backup")
    containers=audit.get("containers")
    if not isinstance(db,dict) or not isinstance(backup,dict) or not isinstance(containers,list):
        raise ValueError("missing audit inventory")
    observations={
        "four_containers_healthy":audit.get("four_containers_healthy") is True,
        "schema7":db.get("schema")==7,
        "live_accounts":db.get("accounts"),
        "no_recorded_leases":db.get("recorded_http_leases")==0,
        "no_nonterminal_tasks":db.get("manual_tasks_nonterminal")==0 and db.get("launch_jobs_nonterminal")==0,
        "account_scheduler_disabled":db.get("scheduler_enabled") is False,
        "historical_backup_sha_verified":backup.get("sha256_match") is True,
        "historical_backup_count_matches":backup.get("account_count_matches_live") is True,
        "original_image_ids_recorded":len(containers)==4 and all(bool(c.get("image_id")) for c in containers),
    }
    blockers=[
        "Old running OCI SDK and detached thread quiescence has not been attested",
        "Ingress freeze and operator-approved maintenance window not established",
        "Current backup verified structurally, but cutover-point consistency/recovery rehearsal not attested",
        "Approved, independently validated live cutover and rollback executor absent",
    ]
    return {
        "status":"BLOCKED_PLAN_ONLY",
        "observations":observations,
        "blockers":blockers,
        "global_oci_quiescence_proven":False,
        "production_cutover_authorized":False,
        "requires_operator_approval":True,
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--audit-json",type=Path,required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if args.apply:
        print(json.dumps({"status":"BLOCKED","production_cutover_authorized":False}))
        return 4
    try:
        audit=json.loads(args.audit_json.read_text())
        report=assess(audit)
    except (OSError,ValueError,TypeError):
        print(json.dumps({"status":"INVALID_AUDIT","production_cutover_authorized":False}))
        return 3
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
