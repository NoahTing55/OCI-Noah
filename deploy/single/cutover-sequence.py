#!/usr/bin/env python3
"""Single-container production cutover sequence specification.

The executor is deliberately NOT armed: no unreviewed lifecycle mutation is
permitted. This module centralizes the mandatory phase dependencies, useful
for later implementation, CI and operator review. No database or Docker I/O.
"""
from __future__ import annotations
import json
from dataclasses import dataclass

PHASES = (
    "operator_approval",
    "verify_exact_four_container_identity",
    "verify_pinned_rollback_images",
    "activate_atomic_global_maintenance_gate",
    "attest_all_oci_http_background_scheduler_and_detached_work_drained",
    "stop_original_four_containers",
    "verify_no_original_processes_or_ports_remain",
    "create_and_verify_final_quiescent_sqlite_snapshot",
    "start_sha_pinned_single_container",
    "verify_four_internal_endpoints_database_and_credentials",
    "record_success_and_reopen_admission",
)
ROLLBACK = (
    "stop_candidate_and_verify_exit",
    "restore_final_snapshot_only_if_db_incompatible",
    "restore_original_four_pinned_images_and_exact_configuration",
    "verify_four_endpoints_and_sqlite_credentials",
    "reopen_admission_only_after_successful_recovery",
)

@dataclass(frozen=True)
class Gate:
    name: str
    evidence: bool

def evaluate(evidence: dict) -> dict:
    # No API or tool in this repository currently proves the complete
    # pre-stop OCI drain or old-image compatibility after migration.
    required = (
        "operator_approved",
        "atomic_global_admission_proven",
        "scheduler_and_detached_work_quiescence_proven",
        "encrypted_credentials_restore_proven",
        "rollback_images_and_config_verified",
        "database_backward_compatibility_proven",
        "docker_socket_threat_review_approved",
    )
    missing=[key for key in required if evidence.get(key) is not True]
    # A caller-provided dict must NEVER grant production authorization.
    return {
        "state":"NOT_ARMED",
        "missing_evidence":missing,
        "phases":list(PHASES),
        "rollback_phases":list(ROLLBACK),
        "release_authorized":False,
        "executor_available":False,
        "note":"Values are planning assertions only, never trusted attestations.",
    }

def main():
    import argparse
    import sys
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if args.apply:
        print(json.dumps({"status":"BLOCKED","reason":"no trusted live admission attestation/executor",
                          "release_authorized":False}))
        return 4
    print(json.dumps(evaluate({}),indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
