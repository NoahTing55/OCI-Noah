from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
work = Path(tempfile.mkdtemp(prefix="oci-nt-v104-db-"))
os.environ.update(
    SECRET_KEY="test-secret-key",
    CREDENTIAL_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    ADMIN_USERNAME="Noah",
    ADMIN_PASSWORD="x",
    DATA_DIR=str(work),
    DB_PATH=str(work / "oci-nt.db"),
    BACKUP_DIR=str(work / "backups"),
)

import sys
sys.path.insert(0, str(root / "backend"))
from app.database import init_database  # noqa: E402

init_database()
with sqlite3.connect(work / "oci-nt.db") as connection:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
required = {
    "users", "oci_accounts", "bulk_check_jobs", "manual_tasks", "manual_task_items",
    "schema_version", "schema_migrations", "proxy_health_history", "proxy_rotation_history",
    "oci_instance_cache", "launch_jobs",
    "launch_attempts", "launch_profiles", "oci_resource_cache", "ip_quality_history",
    "vnc_sessions", "object_multipart_sessions", "audit_logs", "proxy_profiles",
    "backup_restore_drills", "system_upgrade_history",
    "auth_sessions", "login_attempts", "database_restore_history",
    "system_metrics", "system_alert_states", "system_alert_events",
}
missing = sorted(required - tables)
if missing:
    raise SystemExit(f"DB_MIGRATION_FAILED missing={missing}")
with sqlite3.connect(work / "oci-nt.db") as connection:
    version = connection.execute("SELECT version FROM schema_version WHERE singleton_id = 1").fetchone()[0]
    quick = connection.execute("PRAGMA quick_check").fetchone()[0]
if version != 6 or quick != "ok":
    raise SystemExit(f"DB_MIGRATION_FAILED version={version} quick={quick}")
init_database()
with sqlite3.connect(work / "oci-nt.db") as connection:
    migration_count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
if migration_count != 6:
    raise SystemExit(f"DB_MIGRATION_FAILED repeated={migration_count}")
print(f"DB_MIGRATION_OK {len(tables)} schema={version}")
