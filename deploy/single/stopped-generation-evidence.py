#!/usr/bin/env python3
"""Validate read-only stopped-generation evidence; no Docker commands are run.

Evidence is collected separately by an authorized operator after freezing ingress.
This report alone NEVER authorizes deployment or proves absence of external OCI calls.
"""
import argparse
import json
from pathlib import Path

REQUIRED=("oci-nt-api","oci-nt-monitor-agent","oci-nt-docker-guard")

def evaluate(evidence):
    if not isinstance(evidence,dict): raise ValueError("invalid evidence")
    containers=evidence.get("containers")
    if not isinstance(containers,dict) or set(containers)!=set(REQUIRED):
        raise ValueError("incomplete OCI generation inventory")
    stopped={}
    for name,entry in containers.items():
        if not isinstance(entry,dict): raise ValueError("invalid container state")
        # A container's existence is not proof its process has exited.
        stopped[name]=(entry.get("running") is False and
                       entry.get("state") in ("exited","dead") and
                       isinstance(entry.get("pid"),int) and
                       not isinstance(entry.get("pid"),bool) and
                       entry["pid"]==0)
    ingress=evidence.get("ingress_frozen") is True
    return {
        "status":"EXIT_EVIDENCE_COMPLETE_PLAN_ONLY" if all(stopped.values()) and ingress else "BLOCKED_EXIT_EVIDENCE",
        "ingress_frozen":ingress,
        "old_oci_processes_exited":stopped,
        "old_generation_process_exit_evidenced":all(stopped.values()),
        "inflight_sdk_calls_were_drained_before_stop":False,
        "global_external_requests_absent_proven":False,
        "production_cutover_authorized":False,
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--evidence-json",type=Path,required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if args.apply:
        print('{"status":"BLOCKED","production_cutover_authorized":false}')
        return 4
    try:
        result=evaluate(json.loads(args.evidence_json.read_text()))
    except (OSError,TypeError,ValueError):
        print('{"status":"INVALID_EVIDENCE","production_cutover_authorized":false}')
        return 3
    print(json.dumps(result,indent=2))
    return 0 if result["old_generation_process_exit_evidenced"] and result["ingress_frozen"] else 3

if __name__=="__main__":
    raise SystemExit(main())
