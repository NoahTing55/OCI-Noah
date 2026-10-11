#!/usr/bin/env python3
"""Consolidated, read-only production cutover preflight.

Only reports existing evidence; does not grant authorization, alter SQLite,
invoke docker lifecycle operations, emit credentials or expose .env contents.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent

def module(filename):
    spec=importlib.util.spec_from_file_location(filename.replace("-","_").replace(".","_"),HERE/filename)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

def report(*, sha: str, root: Path, runner):
    controller=module("release-controller.py")
    evidence=module("rollback-evidence.py")
    gate=module("rollback-image-gate.py")
    inspection=controller.inspect(sha,runner=runner,root=root)
    manifest=evidence.safe_manifest(inspection)
    images=gate.verify(manifest,runner)
    recorded_idle = inspection.get("recorded_work_drained") is True
    maintenance_active = inspection.get("maintenance_active") is True
    # A read-only inventory can never prove the absence of untracked OCI calls.
    # Preserve failure evidence even if the recorded-work gate has not passed.
    blockers = [
        "No attestation covering all OCI SDK calls and detached threads",
        "No approved production Docker lifecycle executor",
        "No proven old-image boot with the current database and credentials",
        "No explicit operator-approved maintenance window",
    ]
    if not recorded_idle:
        blockers.insert(0, "Recorded OCI leases or nonterminal jobs may still be active")
    if not maintenance_active:
        blockers.insert(0, "Durable maintenance admission is not active")
    return {
        "status":"BLOCKED_RECORDED_WORK" if not recorded_idle else "OBSERVATION_ONLY",
        "blockers":blockers,
        "four_containers":sorted(manifest["original_four_images"]),
        "candidate_image":manifest["candidate_image"],
        "candidate_image_id":manifest["candidate_image_id"],
        "rollback_images_present":images["status"]=="IMAGE_ROLLBACK_PREREQUISITES_PRESENT",
        "original_image_ids":manifest["original_four_image_ids"],
        "maintenance_active":inspection.get("maintenance_active"),
        "recorded_work_drained":inspection.get("recorded_work_drained"),
        "untracked_oci_calls_verified":False,
        "runtime_shutdown_verified":False,
        "credential_compatibility_verified":False,
        "rollback_boot_verified_here":False,
        "release_authorized":False,
        "production_cutover_allowed":False,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--sha",required=True)
    p.add_argument("--root",type=Path,default=Path("/opt/oci-nt"))
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    if a.apply:
        print(json.dumps({"status":"BLOCKED","release_authorized":False}))
        return 4
    try:
        c=module("release-controller.py")
        out=report(sha=a.sha,root=a.root,runner=c.call)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,IndexError,json.JSONDecodeError) as e:
        print(json.dumps({"status":"BLOCKED","reason":type(e).__name__,
                          "release_authorized":False}))
        return 3
    print(json.dumps(out,indent=2))
    return 3 if out["status"]=="BLOCKED_RECORDED_WORK" else 0

if __name__=="__main__":
    raise SystemExit(main())
