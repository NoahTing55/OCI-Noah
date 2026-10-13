#!/usr/bin/env python3
"""Read-only OCI-N&T production observation. NEVER authorizes a cutover.

Reads Docker metadata, file metadata and SQLite via mode=ro/query_only.
Does not invoke docker exec, containers, OCI network, writes or Compose.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess

NAMES=("oci-nt-api","oci-nt-web","oci-nt-monitor-agent","oci-nt-docker-guard")
ROOT=Path("/opt/oci-nt")
BACKUP=Path("/root/oci-nt-backups/oci-nt-schema7-20261012-095820-626576.db")
EXPECTED_BACKUP_SHA="d959aae4c0f3f03688745edd19fd9c9cd1fae4b8ce80df362a3268daf7b25d2c"

def docker_inventory():
    raw=subprocess.run(["docker","inspect",*NAMES],capture_output=True,text=True,check=True,timeout=20)
    values=json.loads(raw.stdout)
    return [{
        "name":x["Name"].lstrip("/"),
        "running":bool(x["State"]["Running"]),
        "health":x["State"].get("Health",{}).get("Status","unknown"),
        "image_id":x["Image"],
        "configured_image":x["Config"].get("Image"),
        "compose_project":x["Config"].get("Labels",{}).get("com.docker.compose.project"),
        "restart":x["HostConfig"]["RestartPolicy"]["Name"],
        "privileged":x["HostConfig"]["Privileged"],
        "docker_socket_mount":any(m.get("Destination")=="/var/run/docker.sock" for m in x.get("Mounts",[])),
    } for x in values]

def database_inventory(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("database missing or symlink")
    # mode=ro sees committed WAL state without opening a write transaction.
    uri=path.resolve().as_uri()+"?mode=ro"
    con=sqlite3.connect(uri,uri=True,timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        version=con.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]
        settings={}
        keys=("oci_nt_release_maintenance_v1","oci_nt_http_operation_leases_v1","account_check_schedule_v1")
        for key in keys:
            row=con.execute("SELECT setting_value FROM system_settings WHERE setting_key=?",(key,)).fetchone()
            try: obj=json.loads(row[0]) if row else None
            except (ValueError,TypeError): obj="invalid"
            if key.endswith("leases_v1"):
                settings["recorded_http_leases"]=len(obj) if isinstance(obj,dict) else None
            elif key=="oci_nt_release_maintenance_v1":
                settings["maintenance_active"]=obj.get("active") if isinstance(obj,dict) else None
            else:
                settings["scheduler_enabled"]=obj.get("enabled") if isinstance(obj,dict) else None
                settings["scheduler_mode"]=obj.get("mode") if isinstance(obj,dict) else None
        terminal={"COMPLETED","PARTIAL","FAILED","CANCELLED","INTERRUPTED","SUCCESS","FINISHED","STOPPED"}
        for table in ("manual_tasks","launch_jobs"):
            rows=con.execute("SELECT status,COUNT(*) FROM "+table+" GROUP BY status").fetchall()
            settings[table+"_nonterminal"]=sum(n for status,n in rows if str(status or "").upper() not in terminal)
        return {"schema":version,"accounts":con.execute("SELECT COUNT(*) FROM oci_accounts").fetchone()[0],**settings}
    finally:
        con.close()

def report(root=ROOT,backup=BACKUP):
    assert root==ROOT, "only official production path supported"
    inv=docker_inventory()
    db=database_inventory(root/"data/oci-nt.db")
    import hashlib
    backup_state={"exists":backup.is_file() and not backup.is_symlink(),"sha256_match":False,"schema":None}
    if backup_state["exists"]:
        h=hashlib.sha256()
        with backup.open("rb") as f:
            for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
        backup_state["sha256_match"]=h.hexdigest()==EXPECTED_BACKUP_SHA
        backup_state["schema"]=database_inventory_schema(backup)
    socket=os.stat("/var/run/docker.sock")
    active=len(inv)==4 and all(x["running"] and x["health"]=="healthy" and x["compose_project"]=="oci-nt" for x in inv)
    return {"status":"READ_ONLY_OBSERVATION","production_cutover_authorized":False,
            "global_oci_quiescence_proven":False,"operator_window_confirmed":False,
            "four_containers_healthy":active,"containers":inv,"database":db,
            "backup":backup_state,"docker_socket":{"uid":socket.st_uid,"gid":socket.st_gid,"mode":oct(socket.st_mode & 0o777)},
            "blockers":["Full OCI SDK and detached worker quiescence unproven",
                        "No operator-approved cutover window",
                        "Rollback evidence must be independently attested live",
                        "No approved production cutover executor"]}

def database_inventory_schema(p):
    c=sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True,timeout=5)
    try:
        c.execute("PRAGMA query_only=ON")
        return c.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]
    finally:c.close()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    a=parser.parse_args()
    if a.apply:
        print(json.dumps({"status":"BLOCKED","production_cutover_authorized":False}))
        return 4
    try:
        result=report()
    except (OSError,ValueError,sqlite3.Error,subprocess.SubprocessError,KeyError,TypeError,AssertionError) as exc:
        print(json.dumps({"status":"AUDIT_FAILED","error_type":type(exc).__name__,"production_cutover_authorized":False}))
        return 3
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
