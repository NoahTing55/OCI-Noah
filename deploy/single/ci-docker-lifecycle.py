#!/usr/bin/env python3
"""Docker lifecycle smoke test strictly scoped to GitHub-hosted disposable CI.

This is NOT a production cutover adapter. It refuses every use outside the
GitHub-hosted runner, and never touches any oci-nt production container.
"""
import json
import os
import re
import subprocess
import sys
import uuid

PREFIX="oci-nt-ephemeral-lifecycle-"
LABEL="oci.nt.ephemeral.lifecycle"
IMAGE="ghcr.io/noahting55/oci-noah-web:ci-switch"

class SafetyError(RuntimeError):
    pass

def docker(*args):
    p=subprocess.run(["docker",*args],text=True,capture_output=True,timeout=40)
    if p.returncode:
        raise SafetyError("Docker command failed")
    return p.stdout.strip()

def require_runner():
    if os.environ.get("GITHUB_ACTIONS")!="true" or os.environ.get("RUNNER_ENVIRONMENT")!="github-hosted":
        raise SafetyError("only disposable GitHub-hosted runners may execute Docker lifecycle")
    if os.path.exists("/opt/oci-nt/data/oci-nt.db") or os.path.exists("/opt/oci--nt"):
        raise SafetyError("production filesystem detected")

def verify(name, identity):
    if not re.fullmatch(PREFIX+r"[a-f0-9]{16}",name):
        raise SafetyError("container name is not a disposable test name")
    payload=json.loads(docker("inspect",name))
    if len(payload)!=1:
        raise SafetyError("ambiguous Docker inspect result")
    c=payload[0]
    if (c.get("Id")!=identity or c.get("Name")!="/"+name or
        (c.get("Config",{}).get("Labels") or {}).get(LABEL)!="true"):
        raise SafetyError("container identity/ownership changed; refusing lifecycle command")
    return c

def main():
    require_runner()
    name=PREFIX+uuid.uuid4().hex[:16]
    identity=None
    try:
        identity=docker("create","--name",name,"--label",LABEL+"=true",
                        "--network","none","--restart","no","--entrypoint","/bin/sh",
                        IMAGE,"-c","sleep 120")
        if not re.fullmatch("[a-f0-9]{64}",identity):
            raise SafetyError("unexpected created ID")
        verify(name,identity)
        docker("start",name)
        if not verify(name,identity)["State"]["Running"]:
            raise SafetyError("disposable container failed to start")
        docker("stop","--time","5",name)
        if verify(name,identity)["State"]["Running"]:
            raise SafetyError("disposable container failed to stop")
        docker("start",name)
        if not verify(name,identity)["State"]["Running"]:
            raise SafetyError("disposable container restart failed")
        print(json.dumps({"status":"EPHEMERAL_DOCKER_LIFECYCLE_VERIFIED",
                          "production_cutover_authorized":False}))
    finally:
        if identity is not None:
            try:
                verify(name,identity)
                docker("rm","-f",name)
            except (SafetyError,ValueError,KeyError):
                print("cleanup requires runner investigation",file=sys.stderr)
                raise

if __name__=="__main__":
    try:
        main()
    except (SafetyError,subprocess.TimeoutExpired,ValueError,KeyError) as exc:
        print(json.dumps({"status":"BLOCKED","reason":type(exc).__name__,
                          "production_cutover_authorized":False}))
        sys.exit(3)
