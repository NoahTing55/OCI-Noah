#!/usr/bin/env python3
"""Read-only SQLite cutover-point snapshot verification, fail closed.

Never creates a backup, touches running production services, or approves
cutover. Input is an offline immutable backup and operator supplied SHA256.
"""
import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

def verify(path,expected_sha,expected_accounts=26):
    if not re.fullmatch(r"[0-9a-f]{64}",expected_sha):
        raise ValueError("invalid expected sha")
    if expected_accounts<1: raise ValueError("invalid account count")
    if not path.is_file() or path.is_symlink(): raise ValueError("invalid snapshot")
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha: raise ValueError("sha256 mismatch")
    with sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok": raise ValueError("integrity")
        schema=db.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()
        if not schema or schema[0]!=7: raise ValueError("schema")
        count=db.execute("SELECT COUNT(*) FROM oci_accounts").fetchone()[0]
        if count!=expected_accounts: raise ValueError("account count mismatch")
    return {"status":"SNAPSHOT_CONTENT_VERIFIED_PLAN_ONLY","schema":7,
            "accounts":count,"sha256_verified":True,
            "cutover_point_consistency_proven":False,
            "snapshot_restore_rehearsed":False,
            "production_cutover_authorized":False}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--snapshot",type=Path,required=True)
    parser.add_argument("--sha256",required=True)
    parser.add_argument("--accounts",type=int,default=26)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if args.apply:
        print('{"status":"BLOCKED","production_cutover_authorized":false}')
        return 4
    try:
        result=verify(args.snapshot,args.sha256,args.accounts)
    except (OSError,ValueError,sqlite3.Error):
        print('{"status":"SNAPSHOT_VERIFICATION_FAILED","production_cutover_authorized":false}')
        return 3
    print(json.dumps(result,indent=2))
    return 0
if __name__=="__main__": raise SystemExit(main())
