from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone

from .backup_service import resolve_backup_path, verify_sqlite_backup
from .config import settings
from .database import database


def _record(backup_name: str, status: str, result: dict, error: str | None, duration_ms: int) -> dict:
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO backup_restore_drills (
                backup_name, status, quick_check, schema_before, schema_after,
                openapi_paths, app_version, duration_ms, error, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                backup_name,
                status,
                result.get("quick_check"),
                result.get("schema_before"),
                result.get("schema_after"),
                result.get("openapi_paths"),
                result.get("app_version"),
                duration_ms,
                error,
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        drill_id = int(cursor.lastrowid)
    return {"id": drill_id, "backup_name": backup_name, "status": status, "duration_ms": duration_ms, "error": error, **result}


def run_backup_restore_drill(backup_name: str, *, timeout_seconds: int = 120) -> dict:
    started = time.monotonic()
    verification = verify_sqlite_backup(backup_name)
    if not verification.get("ok"):
        duration = int((time.monotonic() - started) * 1000)
        return _record(
            backup_name,
            "FAILED",
            verification,
            f"备份 quick_check 失败：{verification.get('quick_check')}",
            duration,
        )

    source = resolve_backup_path(backup_name)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {**verification}
    error: str | None = None
    status = "FAILED"
    with tempfile.TemporaryDirectory(prefix="restore-drill-", dir=settings.data_dir) as temp_dir:
        temp_db = os.path.join(temp_dir, "restore-drill.db")
        shutil.copy2(source, temp_db)
        environment = dict(os.environ)
        environment.update({
            "DB_PATH": temp_db,
            "DATA_DIR": temp_dir,
            "BACKUP_DIR": os.path.join(temp_dir, "backups"),
        })
        process = subprocess.run(
            [sys.executable, "-m", "app.restore_drill_worker", temp_db],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(30, int(timeout_seconds)),
            check=False,
        )
        marker = "RESTORE_DRILL_RESULT="
        for line in reversed(process.stdout.splitlines()):
            if line.startswith(marker):
                try:
                    parsed = json.loads(line[len(marker):])
                    if isinstance(parsed, dict):
                        result.update(parsed)
                except json.JSONDecodeError:
                    pass
                break
        if process.returncode == 0 and result.get("ok"):
            status = "SUCCESS"
        else:
            error = (process.stdout.strip() or f"worker exit={process.returncode}")[-4000:]

    duration = int((time.monotonic() - started) * 1000)
    return _record(backup_name, status, result, error, duration)


def list_restore_drills(limit: int = 30) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with database() as connection:
        rows = connection.execute(
            "SELECT * FROM backup_restore_drills ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result: list[dict] = []
    for row in rows:
        payload = dict(row)
        try:
            payload["result"] = json.loads(payload.pop("result_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload["result"] = {}
        result.append(payload)
    return result
