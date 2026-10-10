#!/usr/bin/env python3
"""Validate proposed four-container rollback Compose without printing secrets.

Never invokes 'up', 'down', 'restart', 'run' or other mutation.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

SERVICES={"api":"oci-nt-api","web":"oci-nt-web","monitor":"oci-nt-monitor-agent","docker-guard":"oci-nt-docker-guard"}
PREFIX="ghcr.io/noahting55/oci-noah-"

def validate(services,expected):
    if set(services)!=set(SERVICES):
        raise ValueError("unexpected Compose service set")
    for name,container in SERVICES.items():
        s=services[name]
        if s.get("container_name")!=container:
            raise ValueError("unexpected container identity")
        if s.get("network_mode")!="host":
            raise ValueError("rollback must preserve host networking")
        expected_image=expected["original_four_images"][container]
        if s.get("image")!=expected_image:
            raise ValueError("rollback image mismatch")
        prefix=PREFIX+("web" if name=="web" else "api")+":sha-"
        if not re.fullmatch(re.escape(prefix)+r"[0-9a-f]{40}",expected_image):
            raise ValueError("rollback image is not immutable")
    return {"status":"ROLLBACK_COMPOSE_PLAN_VALID","original_images_checked":4,
            "release_authorized":False,"production_cutover_allowed":False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--override",type=Path,required=True)
    p.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[2])
    a=p.parse_args()
    try:
        for path in (a.manifest,a.override):
            if path.is_symlink() or not path.is_file():
                raise ValueError("invalid input file")
        manifest=json.loads(a.manifest.read_text(encoding="utf-8"))
        if manifest.get("release_authorized") is not False:
            raise ValueError("manifest must be non-authorizing")
        cmd=["docker","compose","-f",str(a.root/"docker-compose.yml"),
             "-f",str(a.override),"config","--format","json"]
        p=subprocess.run(cmd,cwd=a.root,capture_output=True,text=True,timeout=30,check=False)
        # Compose resolved output may contain secrets: NEVER forward stdout/stderr.
        if p.returncode:
            raise RuntimeError("Compose config failed")
        services=json.loads(p.stdout)["services"]
        report=validate(services,manifest)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,json.JSONDecodeError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,"reason":type(exc).__name__}))
        return 3
    print(json.dumps(report,indent=2))
    return 0

if __name__=="__main__":
    sys.exit(main())
