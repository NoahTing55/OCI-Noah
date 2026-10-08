#!/usr/bin/env python3
"""Isolated candidate schema smoke test; NEVER runs against live SQLite.

Creates a WAL-consistent disposable snapshot, starts the already-local
candidate image with --network none and only fake application credentials,
and invokes its DB initialization on the disposable copy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

IMAGE_PREFIX = "ghcr.io/noahting55/oci-noah-api:sha-"
SHA = re.compile(r"^[0-9a-f]{40}$")

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024*1024), b""):
            h.update(b)
    return h.hexdigest()

def schema(db):
    with sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True) as con:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        row = con.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()
        if not row: raise RuntimeError("Schema version is missing")
        return int(row[0])

def run(db: Path, sha: str, *, runner=subprocess.run):
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid immutable 40-character SHA")
    if not db.is_file():
        raise FileNotFoundError("Source SQLite file not found")
    source_hash = digest(db)
    original_schema = schema(db)
    image = IMAGE_PREFIX + sha
    # The candidate must be locally staged. This does not pull any image.
    inspected = runner(["docker","image","inspect",image],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=20)
    with tempfile.TemporaryDirectory(prefix="oci-nt-candidate-") as tmp:
        work = Path(tmp)
        copy = work / "oci-nt.db"
        with sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True,timeout=30) as src, sqlite3.connect(copy) as dest:
            src.backup(dest)
        before_copy = schema(copy)
        if before_copy != original_schema:
            raise RuntimeError("Source/copy schema mismatch; aborting")
        # No production credentials or .env are passed into the candidate.
        # Do not use network host, Docker socket, or mount any production path.
        work.chmod(0o700)
        copy.chmod(0o600)
        command = [
            "docker","run","--rm","--network","none",
            "--cap-drop","ALL","--security-opt","no-new-privileges",
            "--user",f"{os.getuid()}:{os.getgid()}",
            "--mount",f"type=bind,src={work},dst=/app/data",
            "--tmpfs","/tmp:rw,noexec,nosuid,size=16m",
            "-e","DB_PATH=/app/data/oci-nt.db",
            "-e","DATA_DIR=/app/data",
            "-e","BACKUP_DIR=/app/data/backups",
            "-e","CANDIDATE_MODE=true",
            "-e","SECRET_KEY=candidate-isolated-schema-test-only",
            "-e","CREDENTIAL_ENCRYPTION_KEY=candidate-isolated-schema-test-only",
            "-e","ADMIN_PASSWORD=candidate-isolated-schema-test-only",
            "--entrypoint","python",image,
            "-c","from app.database import init_database; init_database(); print('CANDIDATE_SCHEMA_INIT_OK')",
        ]
        proc = runner(command,check=False,text=True,capture_output=True,timeout=180)
        if proc.returncode != 0 or "CANDIDATE_SCHEMA_INIT_OK" not in proc.stdout:
            raise RuntimeError("Candidate schema initialization failed on disposable copy")
        upgraded_schema = schema(copy)
        if upgraded_schema < original_schema:
            raise RuntimeError('Candidate schema downgrade is not permitted')
    if digest(db) != source_hash:
        raise RuntimeError("Source DB bytes changed during test; abort")
    return {
        "status":"ISOLATED_CANDIDATE_SCHEMA_PASS",
        "source_schema":original_schema,
        "candidate_schema":upgraded_schema,
        "source_unchanged":True,
        "network_disabled":True,
        "production_credentials_used":False,
        "tested_image":image,
        "runtime_compatibility_verified":False,
        "rollback_verified":False,
        "release_authorized":False,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path,required=True)
    p.add_argument("--sha",required=True)
    a=p.parse_args()
    try:
        print(json.dumps(run(a.db,a.sha),indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,sqlite3.Error,subprocess.SubprocessError) as exc:
        print(json.dumps({"status":"BLOCKED","error":type(exc).__name__,"release_authorized":False}))
        return 2

if __name__=="__main__":
    sys.exit(main())
