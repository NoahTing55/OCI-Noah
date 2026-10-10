import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
path = ROOT/"deploy/single/release-controller.py"
spec = importlib.util.spec_from_file_location("single_release_controller",path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SHA = "b0dc8caabade26f5cb383d005e162021965e8c83"

class CutoverControllerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/"data").mkdir()
        (self.root/".env").write_text("SECRET_KEY=synthetic\n")
        (self.root/"docker-compose.yml").write_text("services: {}\n")
        self.db = self.root/"data/oci-nt.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE schema_version(singleton_id INTEGER,version INTEGER);
                INSERT INTO schema_version VALUES (1,7);
                CREATE TABLE system_settings(setting_key TEXT, setting_value TEXT);
                CREATE TABLE manual_tasks(status TEXT);
                CREATE TABLE launch_jobs(status TEXT);
            """)

    def tearDown(self):
        self.temp.cleanup()

    def run_check(self):
        def runner(cmd):
            if cmd[:3]==["docker","image","inspect"]:
                return json.dumps([{"Architecture":"amd64","Id":"sha256:sample"}])
            return json.dumps([{
                "Config":{"Labels":{"com.docker.compose.project":"oci-nt"},"Image":"pinned:test"},
                "State":{"Health":{"Status":"healthy"}}
            }])
        with patch.object(mod.Path,"is_socket",return_value=True):
            original_stat = mod.Path.stat
            def stat_only_socket(p, *args, **kwargs):
                if str(p) == "/var/run/docker.sock":
                    from types import SimpleNamespace
                    return SimpleNamespace(st_gid=997)
                return original_stat(p, *args, **kwargs)
            with patch.object(mod.Path,"stat",stat_only_socket):
                return mod.inspect(SHA,runner=runner,root=self.root)

    def test_healthy_idle_is_still_plan_only(self):
        out=self.run_check()
        self.assertFalse(out["release_authorized"])
        self.assertTrue(out["recorded_work_drained"])
        self.assertFalse(out["execution_supported"])
        self.assertEqual(out["docker_socket_gid"],997)

    def test_active_task_not_authorized(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO launch_jobs VALUES ('RUNNING')")
        out=self.run_check()
        self.assertEqual(out["nonterminal_task_counts"]["launch_jobs"],1)
        self.assertFalse(out["recorded_work_drained"])
        self.assertFalse(out["checks_passed"])
        self.assertEqual(out["recorded_work_gate"],"BLOCKED_ACTIVE_RECORDED_WORK")
        self.assertFalse(out["release_authorized"])

    def test_bad_schema_rejected(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE schema_version SET version=8")
        with self.assertRaisesRegex(RuntimeError,"schema"):
            self.run_check()

    def test_apply_is_always_blocked(self):
        with patch("sys.argv",["release-controller.py","--sha",SHA,"--apply"]):
            self.assertEqual(mod.main(),4)

if __name__=="__main__":
    unittest.main()
