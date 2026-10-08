#!/usr/bin/env python3
"""Restore rehearsal on a disposable SQLite file; never overwrites production."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

def sha(path):
    result=hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda:stream.read(1024*1024),b""):
            result.update(data)
    return result.hexdigest()

def check_schema(conn):
    if conn.execute("PRAGMA quick_check").fetchone()[0]!="ok":
        raise RuntimeError("SQLite integrity failed")
    row=conn.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()
    if row is None: raise RuntimeError("schema_version absent")
    return int(row[0])

def drill(snapshot: Path, expected_sha256: str):
    if not snapshot.is_file(): raise FileNotFoundError("Snapshot missing")
    if len(expected_sha256)!=64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ValueError("Explicit 64-digit SHA-256 required")
    before=sha(snapshot)
    if before!=expected_sha256: raise RuntimeError("Snapshot checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="oci-nt-restore-drill-") as folder:
        target=Path(folder)/"restored.db"
        with sqlite3.connect(f"{snapshot.resolve().as_uri()}?mode=ro",uri=True) as source:
            original_schema=check_schema(source)
            with sqlite3.connect(target) as restored:
                source.backup(restored)
        with sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro",uri=True) as restored:
            restored_schema=check_schema(restored)
        if original_schema!=restored_schema: raise RuntimeError("Restore schema mismatch")
    if sha(snapshot)!=expected_sha256: raise RuntimeError("Snapshot changed during drill")
    return {"result":"DISPOSABLE_RESTORE_VERIFIED","schema":restored_schema,
            "snapshot_unchanged":True,"release_authorized":False,
            "old_image_rollback_verified":False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--snapshot",type=Path,required=True)
    p.add_argument("--sha256",required=True)
    a=p.parse_args()
    try:
        print(json.dumps(drill(a.snapshot,a.sha256),indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
        print(json.dumps({"result":"BLOCKED","release_authorized":False,"reason":type(exc).__name__}))
        return 2

if __name__=="__main__":
    sys.exit(main())
