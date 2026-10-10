#!/usr/bin/env python3
"""Read-only verification of the image-level rollback prerequisites.

Requires a secret-free rollback manifest written by rollback-evidence.py.
Never starts/stops containers, alters Compose or applies database restores.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

EXPECTED = frozenset(("oci-nt-api","oci-nt-web","oci-nt-monitor-agent","oci-nt-docker-guard"))
PREFIX = "ghcr.io/noahting55/oci-noah-"

def verify(manifest: dict, runner):
    if manifest.get("release_authorized") is not False or manifest.get("manifest_version") != 1:
        raise ValueError("bad manifest")
    images = manifest.get("original_four_images")
    if not isinstance(images,dict) or set(images) != EXPECTED:
        raise ValueError("missing original services")
    ids = {}
    for name in sorted(EXPECTED):
        tag = images[name]
        kind = "web" if name == "oci-nt-web" else "api"
        if not isinstance(tag,str) or not re.fullmatch(
            re.escape(PREFIX+kind)+r":sha-[0-9a-f]{40}",tag):
            raise ValueError("original images must be immutable and first-party")
        inspected=json.loads(runner(["docker","image","inspect",tag]))
        if not isinstance(inspected,list) or len(inspected)!=1 or not isinstance(inspected[0],dict):
            raise ValueError("rollback image is absent or inspection invalid")
        item=inspected[0]
        image_id=item.get("Id")
        if not isinstance(image_id,str) or not re.fullmatch("sha256:[0-9a-f]{64}",image_id):
            raise ValueError("invalid Docker image ID")
        if item.get("Architecture")!="amd64":
            raise ValueError("rollback architecture mismatch")
        ids[name]=image_id
    return {
        "status":"IMAGE_ROLLBACK_PREREQUISITES_PRESENT",
        "original_image_ids":ids,
        "image_restore_tested":False,
        "sqlite_restore_tested_here":False,
        "release_authorized":False,
        "production_cutover_allowed":False,
        "note":"Presence is not proof images boot, secrets decrypt or database backward-compatibility."
    }

def run(args):
    p=subprocess.run(args,text=True,capture_output=True,timeout=20,check=False)
    if p.returncode:
        raise RuntimeError("Docker image inspection failed")
    return p.stdout

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",type=Path,required=True)
    ns=parser.parse_args()
    try:
        if ns.manifest.is_symlink():
            raise ValueError("symlink manifest rejected")
        with ns.manifest.open(encoding="utf-8") as f:
            doc=json.load(f)
        result=verify(doc,run)
    except (OSError,RuntimeError,ValueError,TypeError,KeyError,IndexError,json.JSONDecodeError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,"reason":type(exc).__name__}))
        return 3
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0

if __name__=="__main__":
    sys.exit(main())
