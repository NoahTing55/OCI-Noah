import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"deploy/single/production-readonly-audit.py"
spec=importlib.util.spec_from_file_location("audit_rc",SRC)
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

class ReadOnlyAudit(unittest.TestCase):
    def test_never_executes_docker_lifecycle_or_database_write(self):
        s=SRC.read_text()
        for forbidden in ('docker","exec','docker","run','docker","stop','docker","start',
                          'docker","compose','mode=rw','INSERT INTO','UPDATE system_settings','DELETE FROM'):
            self.assertNotIn(forbidden,s)
        for needed in ('"docker","inspect"','?mode=ro','PRAGMA query_only=ON',
                       'global_oci_quiescence_proven":False',
                       'production_cutover_authorized":False'):
            self.assertIn(needed,s)

    def test_apply_denied_without_touching_host(self):
        x=subprocess.run(["python3",str(SRC),"--apply"],capture_output=True,text=True)
        self.assertEqual(x.returncode,4)
        self.assertIn("BLOCKED",x.stdout)

    def test_read_only_report_still_blocks_cutover(self):
        fake=[{"name":n,"running":True,"health":"healthy","compose_project":"oci-nt"} for n in audit.NAMES]
        with patch.object(audit,"docker_inventory",return_value=fake),patch.object(audit,"database_inventory",return_value={"schema":7}),patch.object(audit,"database_inventory_schema",return_value=7),patch.object(audit.os,"stat") as stat,patch.object(audit.Path,"is_file",return_value=False):
            stat.return_value.st_uid=0
            stat.return_value.st_gid=997
            stat.return_value.st_mode=0o660
            out=audit.report()
        self.assertFalse(out["production_cutover_authorized"])
        self.assertFalse(out["global_oci_quiescence_proven"])
        self.assertTrue(out["four_containers_healthy"])
        self.assertTrue(out["blockers"])

if __name__=="__main__":unittest.main()
