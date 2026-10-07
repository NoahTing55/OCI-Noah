from __future__ import annotations

import json
import os
import sqlite3
import sys
import types
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.restore_drill_worker /path/to/database")
    db_path = Path(sys.argv[1]).resolve()
    os.environ["DB_PATH"] = str(db_path)
    os.environ["DATA_DIR"] = str(db_path.parent)
    os.environ["BACKUP_DIR"] = str(db_path.parent / "backups")

    try:
        import oci  # noqa: F401
    except ModuleNotFoundError:
        # The restore drill only builds the OpenAPI schema and never calls OCI.
        # A validation host may not have the OCI SDK installed outside Docker.
        oci_stub = types.ModuleType("oci")
        oci_stub.__version__ = "restore-drill-validation-stub"
        sys.modules["oci"] = oci_stub

    from .config import settings
    from .database import init_database
    from .main import app

    connection = sqlite3.connect(db_path, timeout=30)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        before_row = connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id = 1"
        ).fetchone() if table else None
        schema_before = int(before_row[0] if before_row else 0)
    finally:
        connection.close()

    init_database()

    connection = sqlite3.connect(db_path, timeout=30)
    try:
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        after_row = connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id = 1"
        ).fetchone()
        schema_after = int(after_row[0] if after_row else 0)
        required = {
            "users", "system_settings", "oci_accounts", "manual_tasks",
            "schema_version", "schema_migrations", "system_upgrade_history",
        }
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = sorted(required - tables)
        counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in ("users", "oci_accounts", "manual_tasks")
            if table in tables
        }
    finally:
        connection.close()

    schema = app.openapi()
    result = {
        "ok": quick.lower() == "ok" and not missing,
        "quick_check": quick,
        "schema_before": schema_before,
        "schema_after": schema_after,
        "app_version": settings.version,
        "openapi_paths": len(schema.get("paths", {})),
        "missing_tables": missing,
        "counts": counts,
    }
    print("RESTORE_DRILL_RESULT=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
