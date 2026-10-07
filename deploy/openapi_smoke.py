from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

root = Path(__file__).resolve().parents[1]
work = Path(tempfile.mkdtemp(prefix="oci-nt-v104-openapi-"))
os.environ.update(
    SECRET_KEY="test-secret-key",
    CREDENTIAL_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    ADMIN_USERNAME="Noah",
    ADMIN_PASSWORD="x",
    DATA_DIR=str(work),
    DB_PATH=str(work / "oci-nt.db"),
    BACKUP_DIR=str(work / "backups"),
)
oci = types.ModuleType("oci")
oci.__version__ = "validation-stub"
sys.modules["oci"] = oci
sys.path.insert(0, str(root / "backend"))

from app.main import app  # noqa: E402

schema = app.openapi()
paths = schema.get("paths", {})
required = {
    "/api/v1/proxy/test",
    "/api/v1/tasks",
    "/api/v1/tasks/current",
    "/api/v1/tasks/proxy-health",
    "/api/v1/tasks/instance-batch",
    "/api/v1/accounts/import-preview",
    "/api/v1/tasks/{task_id}",
    "/api/v1/tasks/{task_id}/cancel",
    "/api/v1/tasks/{task_id}/retry",
    "/api/v1/proxies",
    "/api/v1/proxies/{profile_id}",
    "/api/v1/proxies/{profile_id}/test",
    "/api/v1/accounts/{account_id}/proxy",
    "/api/v1/accounts/{account_id}/instances/{instance_id}/network",
    "/api/v1/accounts/{account_id}/vnic-attachments/bulk-delete",
    "/api/v1/accounts/{account_id}/security-lists/{security_list_id}",
    "/api/v1/accounts/{account_id}/object-storage",
    "/api/v1/accounts/{account_id}/launch/profiles/{profile_id}/run-once",
    "/api/v1/accounts/{account_id}/launch/profiles/{profile_id}/preflight",
    "/api/v1/accounts/{account_id}/launch/profiles/{profile_id}/copy",
    "/api/v1/accounts/{account_id}/launch/jobs/{job_id}/retry-failed",
    "/api/v1/accounts/{account_id}/vnc/sessions",
    "/api/v1/accounts/{account_id}/launch/network/ensure",
    "/api/v1/system/release",
    "/api/v1/system/release/history",
    "/api/v1/system/backups/schedule",
    "/api/v1/system/backups/restore-drills",
    "/api/v1/system/backups/{backup_name}/restore-drill",
    "/api/v1/system/backups/{backup_name}/restore",
    "/api/v1/system/database/restores",
    "/api/v1/system/resources",
    "/api/v1/system/resources/limits",
    "/api/v1/auth/logout",
    "/api/v1/auth/sessions",
    "/api/v1/auth/sessions/{session_id}",
    "/api/v1/auth/sessions/logout-others",
    "/api/v1/auth/login-attempts",
    "/api/v1/tasks/{task_id}/safe-resume-preview",
    "/api/v1/tasks/{task_id}/safe-resume",
    "/api/v1/system/monitor/current",
    "/api/v1/system/monitor/history",
    "/api/v1/system/monitor/history.csv",
    "/api/v1/system/monitor/settings",
    "/api/v1/system/monitor/traffic/reset",
    "/api/v1/system/monitor/alerts",
    "/api/v1/system/monitor/history/cleanup",
}
missing = sorted(required - set(paths))
if missing:
    raise SystemExit(f"OPENAPI_FAILED missing={missing}")
if schema.get("info", {}).get("version") != "1.0.4":
    raise SystemExit("OPENAPI_FAILED version")
print(f"OPENAPI_OK {len(paths)} version={schema['info']['version']}")
