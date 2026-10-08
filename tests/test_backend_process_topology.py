import importlib.util
import subprocess
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("topology", Path(__file__).resolve().parents[1]/"deploy/backend-process-topology.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TopologyTests(unittest.TestCase):
    def test_collects_all_containers_without_approval(self):
        seen=[]
        def runner(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd,0,stdout="PID PPID COMMAND\n100 1 python process\n")
        result=mod.audit(runner)
        self.assertEqual(len(seen),3)
        self.assertTrue(all(cmd[:2]==["docker","top"] for cmd in seen))
        self.assertFalse(result["cross_process_quiescence_verified"])
        self.assertFalse(result["release_authorized"])

    def test_unexpected_output_blocks(self):
        def runner(cmd,**kw):
            return subprocess.CompletedProcess(cmd,0,stdout="invalid")
        with self.assertRaises(RuntimeError):
            mod.audit(runner)

    def test_failed_container_blocks(self):
        def runner(cmd, **kw):
            raise subprocess.CalledProcessError(1,cmd)
        with self.assertRaises(subprocess.CalledProcessError):
            mod.audit(runner)


if __name__=="__main__":
    unittest.main()
