#!/usr/bin/env python3
"""Read-only, fail-closed Schema 7 snapshot inventory. Never authorizes cutover.

Supply an independently exported snapshot, its exact SHA256 and expected count.
Production paths and symlinks are forbidden; credentials are never decrypted here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

PRODUCTION=Path("/opt/oci-nt")
INDEPENDENT=Path("/opt/oci--nt")

def inspect_snapshot(path: Path, expected_sha: str, expected_accounts: int) -> dict:
    value=str(path.absolute())
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Explicit regular snapshot file required")
    resolved=path.resolve(strict=True)
    if resolved.is_relative_to(PRODUCTION) or resolved.is_relative_to(INDEPENDENT):
        raise ValueError("Live service trees cannot be used as offline snapshots")
    if expected_accounts < 0 or len(expected_sha)!=64 or any(c not in "0123456789abcdef" for c in expected_sha):
        raise ValueError("Invalid independent snapshot expectations")
    digest=hashlib.sha256()
    with resolved.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            digest.update(block)
    if digest.hexdigest()!=expected_sha:
        raise ValueError("Snapshot SHA256 mismatch")
    db=sqlite3.connect(resolved.as_uri()+"?mode=ro&immutable=1",uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("SQLite integrity check failed")
        version=db.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]
        count=db.execute("SELECT COUNT(*) FROM oci_accounts").fetchone()[0]
    finally:
        db.close()
    if version!=7 or count!=expected_accounts:
        raise ValueError("Snapshot schema or account count differs from expectation")
    return {
        "status":"OFFLINE_SNAPSHOT_STRUCTURE_VERIFIED",
        "schema":version,"accounts":count,"sha256_match":True,
        "credentials_decrypted":False,"candidate_image_verified":False,
        "global_oci_quiescence_proven":False,"production_cutover_authorized":False,
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--snapshot",type=Path,required=True)
    parser.add_argument("--sha256",required=True)
    parser.add_argument("--accounts",type=int,required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if args.apply:
        print(json.dumps({"status":"BLOCKED","production_cutover_authorized":False}))
        return 4
    try:
        report=inspect_snapshot(args.snapshot,args.sha256,args.accounts)
    except (OSError,sqlite3.Error,ValueError,IndexError,TypeError):
        print(json.dumps({"status":"REJECTED","production_cutover_authorized":False}))
        return 3
    print(json.dumps(report))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
