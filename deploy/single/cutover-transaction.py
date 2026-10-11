#!/usr/bin/env python3
"""Cutover transaction coordinator; injectable operations for CI only.

This is the reusable *ordering and compensation* engine, not a production
executor. The CLI never mutates Docker/SQLite and --apply always fails closed.
A future executor must separately prove the global OCI runtime admission gate,
source/target credential equivalence and an approved outage before using it.
"""
from __future__ import annotations
import argparse
import json

class CutoverBlocked(RuntimeError):
    pass

class RecoveryFailed(RuntimeError):
    pass

STEPS = (
    "verify_operator_approval",
    "verify_exact_production_scope",
    "verify_rollback_images",
    "enter_maintenance",
    "attest_complete_oci_quiescence",
    "stop_four",
    "verify_four_stopped",
    "backup_stopped_database",
    "start_single",
    "verify_single",
)
# Admission is deliberately not reopened automatically by this coordinator.
# Database writes after candidate boot require an explicit recovery policy.

def coordinate(ops, *, trusted_test_context: bool = False) -> dict:
    if not trusted_test_context:
        raise CutoverBlocked("production executor not implemented or authorized")
    completed=[]
    old_stopped=False
    candidate_attempted=False
    try:
        for step in STEPS:
            # Track possible side effects BEFORE calling operations. Even an
            # operation raising mid-start must trigger rollback.
            if step=="stop_four":
                old_stopped=True
            if step=="start_single":
                candidate_attempted=True
            getattr(ops,step)()
            completed.append(step)
        return {"status":"SINGLE_HEALTHY_MAINTENANCE_HELD",
                "completed":completed,"admission_reopened":False}
    except BaseException as primary:
        if not old_stopped:
            # Leave maintenance active if it was already entered. An operator
            # must separately assess any incomplete OCI drain and recover.
            raise CutoverBlocked("cutover stopped before old-container shutdown") from primary
        errors=[]
        # Never silently restore a database: candidate may have written data.
        # First preserve failure state and require explicit data compatibility
        # decision by the separate recovery executor.
        for action in (("stop_candidate",candidate_attempted),
                       ("preserve_failed_candidate_database",True),
                       ("verify_database_for_old_release",True),
                       ("restore_four_pinned",True),
                       ("verify_four_healthy",True)):
            name,needed=action
            if not needed:
                continue
            try:
                getattr(ops,name)()
            except BaseException as err:
                errors.append((name,type(err).__name__))
                # Do not restore four if the candidate cannot be stopped,
                # nor if the DB is not proven backward-compatible.
                if name in ("stop_candidate","verify_database_for_old_release"):
                    break
        if errors:
            raise RecoveryFailed("recovery incomplete; maintenance stays active: "+str(errors)) from primary
        return {"status":"FOUR_RESTORED_MAINTENANCE_HELD",
                "completed":completed,"failure_type":type(primary).__name__,
                "admission_reopened":False}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    print(json.dumps({"status":"BLOCKED" if args.apply else "PLAN_ONLY",
                      "reason":"No production operation adapter or trusted admission attestation",
                      "release_authorized":False,
                      "steps":STEPS,
                      "admission_reopened":False}))
    return 4 if args.apply else 0

if __name__=="__main__":
    raise SystemExit(main())
