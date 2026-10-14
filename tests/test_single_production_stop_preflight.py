import pathlib
import subprocess
import sys
import unittest

P=pathlib.Path(__file__).resolve().parents[1]/"deploy/single/production-stop-preflight.py"

class StopPreflight(unittest.TestCase):
    def test_apply_is_always_denied(self):
        x=subprocess.run([sys.executable,str(P),"--apply"],capture_output=True,text=True)
        self.assertEqual(x.returncode,4)
        self.assertIn("DENIED_NO_STOP_IMPLEMENTED",x.stdout)
    def test_no_docker_lifecycle_or_db_writes(self):
        s=P.read_text()
        for forbidden in ('"docker","stop"','"docker","start"','"docker","compose"',"INSERT INTO","DELETE FROM","UPDATE system_settings","mode=rw"):
            self.assertNotIn(forbidden,s)
        self.assertIn('"production_cutover_authorized":False',s)
        self.assertIn('"sdk_quiescence_proven":False',s)

if __name__=="__main__":
    unittest.main()
