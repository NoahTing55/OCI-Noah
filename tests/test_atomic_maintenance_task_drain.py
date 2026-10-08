import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("durable_maintenance", ROOT/"deploy/durable_maintenance.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class AtomicDrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"app.db"
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE system_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,is_secret INTEGER DEFAULT 0,updated_at TEXT)")
            c.execute("CREATE TABLE manual_tasks(id INTEGER,status TEXT)")
            c.execute("CREATE TABLE launch_jobs(id INTEGER,status TEXT)")

    def test_active_manual_task_blocks(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO manual_tasks VALUES(1,'RUNNING')")
        with self.assertRaisesRegex(RuntimeError,"Undrained manual_tasks"):
            m.begin(self.db,operator="test",reason="release")
        self.assertFalse(m.read_state(self.db)["active"])

    def test_pending_launch_blocks(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO launch_jobs VALUES(1,'WAITING')")
        with self.assertRaisesRegex(RuntimeError,"Undrained launch_jobs"):
            m.begin(self.db,operator="test",reason="release")

    def test_unknown_or_null_status_fail_closed(self):
        for status in ("NEW_STATE",None):
            with self.subTest(status=status):
                with sqlite3.connect(self.db) as c:
                    c.execute("DELETE FROM manual_tasks")
                    c.execute("INSERT INTO manual_tasks VALUES(1,?)",(status,))
                with self.assertRaises(RuntimeError):
                    m.begin(self.db,operator="test",reason="release")

    def test_idle_terminal_tasks_allow_marker(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO manual_tasks VALUES(1,'COMPLETED')")
            c.execute("INSERT INTO launch_jobs VALUES(1,'FAILED')")
        m.begin(self.db,operator="test",reason="release")
        self.assertTrue(m.read_state(self.db)["active"])
        m.end(self.db,operator="test")

    def test_missing_table_blocks(self):
        with sqlite3.connect(self.db) as c:
            c.execute("DROP TABLE launch_jobs")
        with self.assertRaisesRegex(RuntimeError,"Required task table missing"):
            m.begin(self.db,operator="test",reason="release")

    def test_serialized_maintenance_after_task_insert(self):
        ready=threading.Event()
        release=threading.Event()
        errors=[]
        def task_writer():
            with sqlite3.connect(self.db, timeout=5, isolation_level=None) as c:
                c.execute("BEGIN IMMEDIATE")
                c.execute("INSERT INTO manual_tasks VALUES(9,'PENDING')")
                ready.set()
                if not release.wait(5):
                    errors.append("writer timeout")
                c.execute("COMMIT")
        t=threading.Thread(target=task_writer)
        t.start()
        self.assertTrue(ready.wait(3))
        try:
            release.set()
            t.join(5)
            self.assertFalse(errors)
            with self.assertRaises(RuntimeError):
                m.begin(self.db,operator="test",reason="release")
        finally:
            release.set()
            t.join(5)

if __name__=="__main__":
    unittest.main()
