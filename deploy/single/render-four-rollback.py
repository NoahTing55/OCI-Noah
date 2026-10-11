#!/usr/bin/env python3
"""Render an immutable four-service Compose rollback override, NEVER apply it.

Use with docker-compose.yml only after a separately authorized operator-reviewed
recovery. No docker subprocesses, stops, starts, DB writes or env disclosure.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import sys

BACKEND = "ghcr.io/noahting55/oci-noah-api:sha-"
WEB = "ghcr.io/noahting55/oci-noah-web:sha-"
NAMES = ("api","docker-guard","monitor","web")
MAPPING = {"api":"oci-nt-api","docker-guard":"oci-nt-docker-guard",
           "monitor":"oci-nt-monitor-agent","web":"oci-nt-web"}

def render(doc:dict)->str:
    if doc.get("manifest_version")!=1 or doc.get("release_authorized") is not False:
        raise ValueError("invalid or falsely authorized evidence")
    originals=doc.get("original_four_images")
    if not isinstance(originals,dict) or set(originals)!=set(MAPPING.values()):
        raise ValueError("incomplete four-container inventory")
    result=["# GENERATED ROLLBACK INVENTORY ONLY — DO NOT RUN WITHOUT SEPARATE AUTHORIZATION",
            "# Does not restore database, certify runtime OCI drain, or prove rollback.",
            "services:"]
    for service in NAMES:
        tag=originals[MAPPING[service]]
        prefix=WEB if service=="web" else BACKEND
        if not isinstance(tag,str) or not re.fullmatch(re.escape(prefix)+r"[0-9a-f]{40}",tag):
            raise ValueError("foreign, mutable or invalid rollback image")
        result.extend((f"  {service}:",f"    image: {tag}"))
    return "\n".join(result)+"\n"

def write_new(out:Path,data:str):
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise ValueError("output parent must be real existing directory")
    if out.is_symlink() or out.exists():
        raise FileExistsError("refusing overwrite")
    old=os.umask(0o077)
    try:
        fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.umask(old)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    try:
        if a.manifest.is_symlink():
            raise ValueError("manifest symlink")
        doc=json.loads(a.manifest.read_text(encoding="utf-8"))
        write_new(a.out,render(doc))
    except (OSError,ValueError,TypeError) as exc:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,"reason":type(exc).__name__}))
        return 3
    print(json.dumps({"status":"PINNED_ROLLBACK_OVERRIDE_WRITTEN","release_authorized":False,
                      "operator_approval_required":True,"path":str(a.out)}))
    return 0

if __name__=="__main__":
    sys.exit(main())
