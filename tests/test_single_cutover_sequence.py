import importlib.util
import unittest
from pathlib import Path

P=Path(__file__).resolve().parents[1]/"deploy/single/cutover-sequence.py"
spec=importlib.util.spec_from_file_location("cutover_sequence",P)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class SequenceContracts(unittest.TestCase):
    def test_do_not_trust_even_all_claims(self):
        report=m.evaluate({k:True for k in (
            "operator_approved","atomic_global_admission_proven",
            "scheduler_and_detached_work_quiescence_proven",
            "encrypted_credentials_restore_proven",
            "rollback_images_and_config_verified",
            "database_backward_compatibility_proven",
            "docker_socket_threat_review_approved")})
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["executor_available"])
    def test_backup_only_after_stop(self):
        self.assertLess(m.PHASES.index("stop_original_four_containers"),
                        m.PHASES.index("create_and_verify_final_quiescent_sqlite_snapshot"))
    def test_recovery_before_reopening(self):
        self.assertLess(m.ROLLBACK.index("verify_four_endpoints_and_sqlite_credentials"),
                        m.ROLLBACK.index("reopen_admission_only_after_successful_recovery"))
    def test_no_runtime_mutations_in_specification(self):
        s=P.read_text()
        self.assertNotIn("subprocess.run(",s)
        self.assertNotIn("docker compose down",s)

if __name__=="__main__":
    unittest.main()
