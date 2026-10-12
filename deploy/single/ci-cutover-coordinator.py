#!/usr/bin/env python3
"""Exercise the actual cutover coordinator with real *disposable* Docker I/O.

Never operates on oci-nt-* production container names or files. The 4->1->4
integration remains independently covered by ci-four-single-four.sh. This test
bridges the coordinator and Docker identity-safe operations with fault injection.
"""
import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

coordinator=load("cutover_transaction_ci",ROOT/"deploy/single/cutover-transaction.py")
docker_scope=load("ci_docker_lifecycle",ROOT/"deploy/single/ci-docker-lifecycle.py")

class DisposableOps:
    def __init__(self,fail=None):
        self.fail=fail
        self.name=docker_scope.PREFIX+uuid.uuid4().hex[:16]
        self.identity=None
        self.calls=[]
        self.tmp=tempfile.TemporaryDirectory(prefix='oci-nt-disposable-cutover-')
        self.db=Path(self.tmp.name)/'synthetic.db'
        self.backup=Path(self.tmp.name)/'final-snapshot.db'
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE marker (value TEXT NOT NULL)')
            con.execute("INSERT INTO marker VALUES ('original-four')")
    def _record(self,step):
        self.calls.append(step)
        if self.fail==step:
            raise RuntimeError("injected failure: "+step)
    def verify_operator_approval(self): self._record("verify_operator_approval")
    def verify_exact_production_scope(self):
        # CI never claims production scope validation; validate that this
        # test's protected namespace cannot be a real production container.
        self._record("verify_exact_production_scope")
        if not self.name.startswith(docker_scope.PREFIX):
            raise RuntimeError("unexpected test namespace")
    def verify_rollback_images(self): self._record("verify_rollback_images")
    def enter_maintenance(self): self._record("enter_maintenance")
    def attest_complete_oci_quiescence(self):
        # Synthetic OCI-free runner only, NOT a production attestation.
        self._record("attest_complete_oci_quiescence")
    def stop_four(self): self._record("stop_four")
    def verify_four_stopped(self): self._record("verify_four_stopped")
    def backup_stopped_database(self):
        self._record("backup_stopped_database")
        with sqlite3.connect(self.db) as src, sqlite3.connect(self.backup) as dst:
            src.backup(dst)
        with sqlite3.connect(self.backup) as con:
            if con.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                raise RuntimeError("backup corrupted")
            if con.execute("SELECT value FROM marker").fetchone()[0]!="original-four":
                raise RuntimeError("snapshot not consistent")
    def verify_stopped_backup_integrity(self):
        self._record("verify_stopped_backup_integrity")
        if not self.backup.is_file():
            raise RuntimeError("stopped backup missing")
        with sqlite3.connect(self.backup) as con:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("stopped backup integrity failed")
            if con.execute("SELECT value FROM marker").fetchall() != [("original-four",)]:
                raise RuntimeError("stopped backup content mismatch")
        # SQLite backup API provides a consistent logical snapshot; its DB
        # header/page layout may differ from the source byte-for-byte.
        with sqlite3.connect(self.db) as source, sqlite3.connect(self.backup) as target:
            if source.execute("SELECT value FROM marker ORDER BY rowid").fetchall() != target.execute("SELECT value FROM marker ORDER BY rowid").fetchall():
                raise RuntimeError("stopped backup logical records differ")

    def verify_backup_old_release_readability(self):
        self._record("verify_backup_old_release_readability")
        with sqlite3.connect(f"{self.backup.resolve().as_uri()}?mode=ro", uri=True) as con:
            con.execute("PRAGMA query_only=ON")
            if con.execute("SELECT value FROM marker").fetchone() != ("original-four",):
                raise RuntimeError("old release cannot read synthetic backup marker")

    def start_single(self):
        self._record("start_single")
        self.identity=docker_scope.docker(
            "create","--name",self.name,"--label",docker_scope.LABEL+"=true",
            "--network","none","--restart","no","--entrypoint","/bin/sh",
            docker_scope.IMAGE,"-c","sleep 120")
        docker_scope.verify(self.name,self.identity)
        docker_scope.docker("start",self.name)
    def verify_single(self):
        self._record("verify_single")
        if not docker_scope.verify(self.name,self.identity)["State"]["Running"]:
            raise RuntimeError("candidate not running")
    def stop_candidate(self):
        self._record("stop_candidate")
        docker_scope.verify(self.name,self.identity)
        docker_scope.docker("stop","--time","5",self.name)
    def preserve_failed_candidate_database(self):
        self._record("preserve_failed_candidate_database")
        # Preserve failed candidate state; never overwrite with backup silently.
        if not self.backup.exists():
            raise RuntimeError("final snapshot absent")
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO marker VALUES ('failed-candidate')")
    def verify_database_for_old_release(self):
        self._record("verify_database_for_old_release")
        with sqlite3.connect(self.db) as con:
            if con.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                raise RuntimeError("synthetic data incompatible")
    def restore_four_pinned(self): self._record("restore_four_pinned")
    def verify_four_healthy(self):
        self._record("verify_four_healthy")
        with sqlite3.connect(self.db) as con:
            count=con.execute("SELECT COUNT(*) FROM marker").fetchone()[0]
            if count!=2: raise RuntimeError("failed candidate data was discarded")
    def cleanup(self):
        try:
            if self.identity:
                docker_scope.verify(self.name,self.identity)
                docker_scope.docker("rm","-f",self.name)
        finally:
            self.tmp.cleanup()

def scenario(injected_failure):
    op=DisposableOps(fail=injected_failure)
    try:
        report=coordinator.coordinate(op,trusted_test_context=True)
        expected=("FOUR_RESTORED_MAINTENANCE_HELD" if injected_failure
                  else "SINGLE_HEALTHY_MAINTENANCE_HELD")
        if report["status"]!=expected or report["admission_reopened"] is not False:
            raise AssertionError(f"unexpected coordinator outcome: {report['status']} {op.calls[-6:]}")
        if injected_failure and "restore_four_pinned" not in op.calls:
            raise AssertionError(f"rollback did not execute; last stages={op.calls[-6:]}")
        return report["status"]
    except Exception as exc:
        print(json.dumps({"status":"CI_SCENARIO_STAGE_FAILED",
                          "fault_injection":injected_failure or "none",
                          "last_stages":op.calls[-8:],
                          "reason":type(exc).__name__,
                          "release_authorized":False}))
        raise
    finally:
        op.cleanup()

def main():
    docker_scope.require_runner()
    if os.environ.get("OCI_NT_CI_COORDINATOR")!="disposable-only":
        raise docker_scope.SafetyError("explicit CI test enablement required")
    healthy=scenario(None)
    recovered=scenario("verify_single")
    print(json.dumps({"status":"COORDINATOR_REAL_DOCKER_CI_OK",
                      "healthy_case":healthy,"rollback_case":recovered,
                      "release_authorized":False}))

if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status":"CI_SCENARIO_FAILED","reason":type(exc).__name__,
                          "release_authorized":False}))
        sys.exit(3)
