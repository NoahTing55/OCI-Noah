import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

atomic = load("atomic_admission", "deploy/atomic_admission.py")
marker = load("durable_maintenance", "deploy/durable_maintenance.py")


class AtomicAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY, setting_value TEXT, is_secret INTEGER DEFAULT 0, updated_at TEXT)")
            con.execute("CREATE TABLE manual_tasks(id INTEGER PRIMARY KEY, status TEXT)")
            con.execute("CREATE TABLE launch_jobs(id INTEGER PRIMARY KEY, status TEXT)")

    def test_admitted_task_and_blocked_after_gate(self):
        with atomic.admitted_transaction(self.db) as con:
            con.execute("INSERT INTO manual_tasks VALUES(1,'RUNNING')")
        # Task insertion is admitted, but release maintenance must wait
        # until the task reaches a terminal state.
        with self.assertRaisesRegex(RuntimeError, "Undrained manual_tasks"):
            marker.begin(self.db, operator="operator", reason="release")
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE manual_tasks SET status='COMPLETED' WHERE id=1")
        marker.begin(self.db, operator="operator", reason="release")
        with self.assertRaises(atomic.MaintenanceBlocked):
            with atomic.admitted_transaction(self.db) as con:
                con.execute("INSERT INTO manual_tasks VALUES(2,'RUNNING')")
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM manual_tasks").fetchone()[0], 1)

    def test_rollback_on_failure(self):
        with self.assertRaises(ValueError):
            with atomic.admitted_transaction(self.db) as con:
                con.execute("INSERT INTO manual_tasks VALUES(3,'RUNNING')")
                raise ValueError("crash")
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM manual_tasks").fetchone()[0], 0)

    def test_begin_waits_for_in_flight_admission(self):
        entered = threading.Event()
        release = threading.Event()
        errors = []
        def worker():
            try:
                with atomic.admitted_transaction(self.db) as con:
                    con.execute("INSERT INTO manual_tasks VALUES(4,'RUNNING')")
                    entered.set()
                    if not release.wait(8):
                        raise RuntimeError("timeout")
            except BaseException as exc:
                errors.append(str(exc))
        worker_thread = threading.Thread(target=worker)
        worker_thread.start()
        self.assertTrue(entered.wait(5))
        started = threading.Event()
        completed = threading.Event()
        def gate():
            started.set()
            try:
                marker.begin(self.db, operator="operator", reason="release")
            except BaseException as exc:
                errors.append(str(exc))
            finally:
                completed.set()
        gate_thread = threading.Thread(target=gate)
        gate_thread.start()
        self.assertTrue(started.wait(5))
        self.assertFalse(completed.wait(0.1))
        release.set()
        worker_thread.join(8)
        gate_thread.join(8)
        self.assertFalse(worker_thread.is_alive())
        self.assertFalse(gate_thread.is_alive())
        # Maintenance first waits for the admission transaction, then
        # rejects its RUNNING task instead of incorrectly entering the gate.
        self.assertEqual(errors, ["Undrained manual_tasks tasks: 1"])
        self.assertFalse(marker.read_state(self.db)["active"])
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM manual_tasks").fetchone()[0], 1)
            con.execute("UPDATE manual_tasks SET status='COMPLETED' WHERE id=4")
        marker.begin(self.db, operator="operator", reason="release")
        self.assertTrue(marker.read_state(self.db)["active"])


if __name__ == "__main__":
    unittest.main()
