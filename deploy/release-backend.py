#!/usr/bin/env python3
"""Fail-closed backend-only release plan. Never applies a release."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SERVICES = {"api": "oci-nt-api", "monitor": "oci-nt-monitor-agent", "docker-guard": "oci-nt-docker-guard"}
IMAGE = "ghcr.io/noahting55/oci-noah-api:sha-"

def cmd(args, env=None, timeout=25):
    return subprocess.run(args, cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, check=True, timeout=timeout).stdout

def plan(sha, run=cmd):
    if not re.fullmatch("[0-9a-f]{40}", sha):
        raise ValueError("A full 40-digit immutable SHA is required")
    root=Path(__file__).resolve().parents[1]
    for f in (".env", "docker-compose.yml", "deploy/compose.backend-release.yml"):
        if not (root/f).is_file():
            raise RuntimeError("Missing required deployment file: "+f)
    target=IMAGE+sha
    env=os.environ.copy()
    env["OCI_NT_RELEASE_BACKEND_IMAGE"]=target
    compose=["docker","compose","-f","docker-compose.yml","-f","deploy/compose.backend-release.yml"]
    services=json.loads(run(compose+["config","--format","json"],env=env))["services"]
    for svc in SERVICES:
        if services[svc]["image"]!=target:
            raise RuntimeError("Image override mismatch for "+svc)
    # Do not display full compose config (contains secrets).
    if services["web"]["image"]==target:
        raise RuntimeError("Web image unexpectedly matches backend candidate")
    running={}
    for svc,name in SERVICES.items():
        state=json.loads(run(["docker","inspect",name]))[0]
        if state.get("Config",{}).get("Labels",{}).get("com.docker.compose.project")!="oci-nt":
            raise RuntimeError("Wrong compose project: "+name)
        healthy=state.get("State",{}).get("Health",{}).get("Status")=="healthy"
        running[name]={"image":state["Config"]["Image"],"healthy":healthy}
        if not healthy:
            raise RuntimeError("Unhealthy service: "+name)
    web=json.loads(run(["docker","inspect","oci-nt-web"]))[0]
    if web.get("Config",{}).get("Labels",{}).get("com.docker.compose.project")!="oci-nt":
        raise RuntimeError("Wrong Web compose project")
    if web.get("State",{}).get("Health",{}).get("Status")!="healthy":
        raise RuntimeError("Current Web unhealthy")
    candidate=json.loads(run(["docker","image","inspect",target]))[0]
    return {"mode":"PLAN_ONLY","candidate":target,"candidate_image_id":candidate["Id"],
            "backend_services":running,"web_unmodified":True,
            "web_image":web["Config"]["Image"],
            "release_authorized":False,"execution_supported":False,
            "blocking_reason":"Old production API cannot attest cross-process OCI work and detached requests are drained."}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--sha",required=True)
    parser.add_argument("--apply",action="store_true",help="unsupported: always refused")
    args=parser.parse_args()
    if args.apply:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,
                          "reason":"--apply is not implemented until safe cross-process drain"}))
        return 4
    try:
        result=plan(args.sha)
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError,KeyError,TypeError,IndexError) as exc:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,"reason":type(exc).__name__}))
        return 3
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    sys.exit(main())
