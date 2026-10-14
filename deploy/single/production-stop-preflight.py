#!/usr/bin/env python3
"""Fail-closed production stop preflight. READ ONLY; no stop implementation.

This is an inventory gate, not an OCI SDK drain attestation or cutover approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

HOST="spartan-dalkvm"
PROJECT=Path("/opt/oci-nt")
RECOVERY=Path("/root/oci-nt-backups/pre-v2-recovery-20261014-113008")
BACKUP=Path("/root/oci-nt-backups/oci-nt-v2-precutover-20261014-112843.db")
COMPOSE_HASH="497417a52b8ea1a7304a3a510ff6dfb2600731e67953e101966f349c3d8b39dc"
BACKUP_HASH="349993b34c1edb52ec3b5493c12386c7d42ef607a6bd2863cfad765fbbd1f9bb"
NAMES=("oci-nt-api","oci-nt-web","oci-nt-monitor-agent","oci-nt-docker-guard")
IMAGES={"oci-nt-api":"sha256:d67ca61ac5129207e421d67834f5daa727aab1162541efee1f23dc1abb90bc27",
"oci-nt-web":"sha256:21fbeac7176e41ba59773fba21887d05d10a6632f78bb61dd73e2d2f4d8002b4",
"oci-nt-monitor-agent":"sha256:d67ca61ac5129207e421d67834f5daa727aab1162541efee1f23dc1abb90bc27",
"oci-nt-docker-guard":"sha256:d67ca61ac5129207e421d67834f5daa727aab1162541efee1f23dc1abb90bc27"}

def digest(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()

def check():
    failures=[]
    if subprocess.check_output(["hostname"],text=True,timeout=5).strip()!=HOST:
        failures.append("unexpected host")
    for p,expected in ((PROJECT/"docker-compose.yml",COMPOSE_HASH),
                       (RECOVERY/"docker-compose.yml",COMPOSE_HASH),
                       (BACKUP,BACKUP_HASH)):
        if not p.is_file() or p.is_symlink() or digest(p)!=expected:
            failures.append("missing or mismatched "+str(p))
    env=RECOVERY/".env"
    if not env.is_file() or env.is_symlink() or not (PROJECT/".env").is_file() or digest(env)!=digest(PROJECT/".env"):
        failures.append("recovery environment mismatch")
    try:
        c=sqlite3.connect(BACKUP.as_uri()+"?mode=ro&immutable=1",uri=True)
        try:
            c.execute("PRAGMA query_only=ON")
            if c.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or c.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]!=7 or c.execute("SELECT COUNT(*) FROM oci_accounts").fetchone()[0]!=26:
                failures.append("snapshot data invalid")
        finally:c.close()
    except sqlite3.Error:
        failures.append("snapshot cannot be read")
    data=json.loads(subprocess.check_output(["docker","inspect",*NAMES,"oci--nt-app-1"],text=True,timeout=20))
    records={x["Name"].lstrip("/"):x for x in data}
    for name in NAMES:
        x=records[name]
        if x["Image"]!=IMAGES[name] or not x["State"]["Running"] or (x["Config"].get("Labels") or {}).get("com.docker.compose.project")!="oci-nt":
            failures.append("legacy container drift: "+name)
    if not records["oci--nt-app-1"]["State"]["Running"]:
        failures.append("independent container not running")
    return {"status":"PREFLIGHT_OBSERVED_NO_GO" if not failures else "PREFLIGHT_FAILED_NO_GO",
            "checks_passed":not failures,"errors":failures,"sdk_quiescence_proven":False,
            "ingress_frozen_proven":False,"cutover_point_snapshot_created":False,
            "production_cutover_authorized":False,"containers_modified":False}

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--apply",action="store_true")
    x=a.parse_args()
    if x.apply:
        print(json.dumps({"status":"DENIED_NO_STOP_IMPLEMENTED","production_cutover_authorized":False}))
        return 4
    try:
        out=check()
        print(json.dumps(out,ensure_ascii=False,indent=2))
        return 0 if out["checks_passed"] else 3
    except (OSError,ValueError,KeyError,IndexError,subprocess.SubprocessError,sqlite3.Error) as e:
        print(json.dumps({"status":"PREFLIGHT_ERROR_NO_GO","error_type":type(e).__name__,"production_cutover_authorized":False}))
        return 3

if __name__=="__main__":
    raise SystemExit(main())
