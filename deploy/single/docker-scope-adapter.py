#!/usr/bin/env python3
"""Strict production Docker scope adapter, read-only by default.

The adapter never invokes container lifecycle changes in production.
CI can inject a fake runner and explicitly mark a *disposable* target; this
does not authorize production execution or attest OCI request quiescence.
"""
from __future__ import annotations
import json
import re
import subprocess

FOUR=("oci-nt-api","oci-nt-web","oci-nt-monitor-agent","oci-nt-docker-guard")
PROJECT="oci-nt"
PRODUCTION_ROOT="/opt/oci-nt"
EXCLUDED_ROOT="/opt/oci--nt"

class ScopeError(RuntimeError):
    pass

def subprocess_runner(argv):
    result=subprocess.run(argv,capture_output=True,text=True,check=False,timeout=20)
    if result.returncode:
        raise ScopeError("Docker scope probe failed")
    return result.stdout

def validate_four(runner=subprocess_runner):
    result={}
    for name in FOUR:
        reply=json.loads(runner(["docker","inspect",name]))
        if not isinstance(reply,list) or len(reply)!=1:
            raise ScopeError("missing or ambiguous original container")
        item=reply[0]
        config=item.get("Config") or {}
        labels=config.get("Labels") or {}
        if item.get("Name")!="/"+name:
            raise ScopeError("container identity mismatch")
        if labels.get("com.docker.compose.project")!=PROJECT:
            raise ScopeError("unexpected compose ownership")
        if labels.get("com.docker.compose.service") not in ("api","web","monitor","docker-guard"):
            raise ScopeError("unexpected service label")
        # Explicitly reject any named mount of the independent deployment.
        for mount in item.get("Mounts",[]):
            src=mount.get("Source","")
            if src==EXCLUDED_ROOT or src.startswith(EXCLUDED_ROOT+"/"):
                raise ScopeError("independent project mount detected")
        result[name]={"id":item.get("Id"),"image_id":item.get("Image")}
        if not isinstance(result[name]["id"],str) or not re.fullmatch("[a-f0-9]{64}",result[name]["id"]):
            raise ScopeError("invalid container ID")
        if not isinstance(result[name]["image_id"],str) or not re.fullmatch("sha256:[a-f0-9]{64}",result[name]["image_id"]):
            raise ScopeError("invalid image identity")
    return {"status":"FOUR_SCOPE_VERIFIED","containers":result,
            "release_authorized":False}

def lifecycle(*args, **kwargs):
    raise ScopeError("production Docker lifecycle adapter is not armed")

if __name__=="__main__":
    print(json.dumps({"status":"PLAN_ONLY","release_authorized":False,
                      "note":"Use tested inventory only; no production lifecycle actions."}))
