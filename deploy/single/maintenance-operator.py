#!/usr/bin/env python3
"""Explicit, operator-only durable OCI maintenance admission control.

Does not deploy, stop containers, snapshot databases or authorize cutover.
BEGIN is an atomic SQLite admission barrier for the code paths that use the
shared lease/transaction protocol; detached/untracked activity is not proven.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

MODULE = Path(__file__).resolve().parents[1] / "durable_maintenance.py"

def load():
    spec=importlib.util.spec_from_file_location("oci_durable_maintenance",MODULE)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

def execute(action, db, *, operator=None, reason=None, confirmation=None, maintenance=None):
    m=maintenance or load()
    if db.is_symlink() or not db.is_file():
        raise RuntimeError("expected existing regular SQLite database")
    if action=="status":
        state=m.read_state(db)
        return {"action":"status","maintenance_active":state.get("active") is True,
                "release_authorized":False}
    if action=="begin":
        if confirmation!="BEGIN OCI-N&T MAINTENANCE":
            raise RuntimeError("explicit BEGIN confirmation required")
        if not operator or not reason:
            raise RuntimeError("operator and reason required")
        state=m.begin(db,operator=operator,reason=reason)
        return {"action":"begin","maintenance_active":state["active"],
                "release_authorized":False,"note":"No production cutover authorized; check all background OCI egress separately."}
    if action=="end":
        if confirmation!="END OCI-N&T MAINTENANCE":
            raise RuntimeError("explicit END confirmation required")
        if not operator:
            raise RuntimeError("operator required")
        state=m.end(db,operator=operator)
        return {"action":"end","maintenance_active":state["active"],
                "release_authorized":False}
    raise ValueError("unsupported maintenance action")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=("status","begin","end"))
    parser.add_argument("--db",type=Path,required=True)
    parser.add_argument("--operator")
    parser.add_argument("--reason")
    parser.add_argument("--confirm")
    a=parser.parse_args()
    try:
        report=execute(a.action,a.db,operator=a.operator,reason=a.reason,confirmation=a.confirm)
    except (RuntimeError,ValueError,OSError,json.JSONDecodeError) as exc:
        print(json.dumps({"status":"BLOCKED","reason":type(exc).__name__,"release_authorized":False}))
        return 3
    print(json.dumps(report,ensure_ascii=False))
    return 0

if __name__=="__main__":
    sys.exit(main())
