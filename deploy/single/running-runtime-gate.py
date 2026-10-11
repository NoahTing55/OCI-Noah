#!/usr/bin/env python3
"""Read-only release gate: running OCI workers must be proven lease-aware.

A checked-out source tree or empty SQLite leases do not attest that the
old *running* API/monitor/guard images contain release-admission controls.
This module never authorizes a production cutover.
"""
from __future__ import annotations

import json
import re

EXPECTED = {
    "oci-nt-api": "api",
    "oci-nt-monitor-agent": "api",
    "oci-nt-docker-guard": "api",
    "oci-nt-web": "web",
}


def inspect_runtime(plan: dict) -> dict:
    if plan.get("mode") != "PLAN_ONLY" or plan.get("release_authorized") is not False:
        raise ValueError("invalid release plan")
    original = plan.get("original_images")
    if not isinstance(original, dict) or set(original) != set(EXPECTED):
        raise ValueError("incomplete running image inventory")
    unverifiable = []
    for name, kind in EXPECTED.items():
        tag = original[name]
        if not isinstance(tag, str) or not re.fullmatch(
            rf"ghcr\.io/noahting55/oci-noah-{kind}:sha-[a-f0-9]{{40}}", tag
        ):
            raise ValueError("unexpected running image tag")
        # Tags are commit references, not proof of what checks run inside
        # the processes. A source repository at a newer commit cannot
        # retroactively protect a running OCI request.
        if name != "oci-nt-web":
            unverifiable.append(name)
    return {
        "status": "BLOCKED_RUNNING_OCI_ATTESTATION",
        "running_oci_services_needing_proof": sorted(unverifiable),
        "recorded_work_drained": plan.get("recorded_work_drained") is True,
        "leases_are_global_quiescence_proof": False,
        "running_generation_attested": False,
        "production_cutover_allowed": False,
        "release_authorized": False,
    }


def main():
    import argparse
    import importlib.util
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        print(json.dumps({"status":"BLOCKED","release_authorized":False}))
        return 4
    filename = Path(__file__).with_name("release-controller.py")
    spec = importlib.util.spec_from_file_location("single_release_controller",filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        result = inspect_runtime(mod.inspect(args.sha))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status":"BLOCKED","reason":type(error).__name__,
                          "release_authorized":False}))
        return 3
    print(json.dumps(result,indent=2))
    return 3


if __name__=="__main__":
    raise SystemExit(main())
