import importlib.util
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("isolated_candidate",ROOT/"deploy/isolated-candidate-schema.py")
tool=importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

class IsolatedCandidateTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"live.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE schema_version(singleton_id INTEGER PRIMARY KEY,version INTEGER)")
            con.execute("INSERT INTO schema_version VALUES(1,7)")

    def test_success_never_authorizes_release(self):
        called=[]
        def runner(cmd,**kw):
            called.append(cmd)
            if cmd[:3]==["docker","image","inspect"]:
                return subprocess.CompletedProcess(cmd,0)
            self.assertEqual(cmd[:2],["docker","run"])
            self.assertIn("--network",cmd)
            self.assertEqual(cmd[cmd.index("--network")+1],"none")
            self.assertNotIn("--env-file",cmd)
            self.assertNotIn("/var/run/docker.sock"," ".join(cmd))
            return subprocess.CompletedProcess(cmd,0,stdout="CANDIDATE_SCHEMA_INIT_OK\n",stderr="")
        before=tool.digest(self.db)
        result=tool.run(self.db,"a"*40,runner=runner)
        self.assertEqual(tool.digest(self.db),before)
        self.assertEqual(result["source_schema"],7)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["rollback_verified"])
        self.assertFalse(result["runtime_compatibility_verified"])
        self.assertEqual(len(called),2)

    def test_failed_candidate_does_not_change_source(self):
        def runner(cmd,**kw):
            if cmd[:3]==["docker","image","inspect"]:
                return subprocess.CompletedProcess(cmd,0)
            return subprocess.CompletedProcess(cmd,1,stdout="",stderr="failure")
        before=tool.digest(self.db)
        with self.assertRaises(RuntimeError):
            tool.run(self.db,"b"*40,runner=runner)
        self.assertEqual(tool.digest(self.db),before)

    def test_invalid_sha_refuses_before_docker(self):
        def fail(*a,**kw):
            self.fail("docker should not execute")
        with self.assertRaises(ValueError):
            tool.run(self.db,"latest",runner=fail)

    def test_no_schema_change_to_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            tool.run(self.db.parent/"missing.db","a"*40)

if __name__=="__main__":
    unittest.main()
