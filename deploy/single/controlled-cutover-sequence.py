#!/usr/bin/env python3
"""Non-executable controlled cutover sequence contract.

No Docker commands, network operations, maintenance toggles or database writes.
This is review-only. CI checks ordering and mandatory blockers; never authorizes
a live transition by itself.
"""
import json

STEPS=(
    ("review","operator_approves_window"),
    ("preserve","verify_current_consistent_backup_and_rollback_images"),
    ("freeze","freeze_external_ingress_and_new_oci_requests"),
    ("drain","wait_for_old_requests_and_workers_to_finish"),
    ("stop","stop_old_api_and_monitor_then_verify_process_exit"),
    ("verify","verify_old_generation_has_no_remaining_sdk_executors"),
    ("snapshot","capture_verified_cutover_point_snapshot"),
    ("start","start_isolated_guard_and_candidate_using_approved_data"),
    ("verify_new","verify_candidate_health_and_credential_compatibility"),
    ("decision","explicit_operator_acceptance_or_rollback"),
    ("rollback","if_failed_stop_candidate_and_restore_legacy_four_services"),
)
BLOCKERS=(
    "Existing running generation lacks global OCI SDK quiescence attestation",
    "Ingress freeze has not been executed or proven",
    "No approved maintenance window or cutover executor",
    "Live final-state rollback readiness not verified",
)

def plan():
    return {
        "status":"PLAN_ONLY_BLOCKED",
        "steps":[{"phase":phase,"action":action} for phase,action in STEPS],
        "blockers":list(BLOCKERS),
        "can_run_on_production":False,
        "operator_approval_required":True,
        "sdk_global_quiescence_proven":False,
        "cutover_authorized":False,
        "production_changes_performed":False,
    }

if __name__=="__main__":
    print(json.dumps(plan(),ensure_ascii=False,indent=2))
