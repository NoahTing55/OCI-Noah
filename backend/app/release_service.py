from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .database import database

_MANIFEST_PATH = Path(__file__).with_name("release_manifest.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_release_manifest() -> dict:
    fallback = {
        "version": settings.version,
        "display_version": settings.display_version,
        "build_id": f"v{settings.version}",
        "source_fingerprint": "unknown",
        "channel": "stable",
        "built_at": None,
    }
    try:
        payload = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return fallback
    if not isinstance(payload, dict):
        return fallback
    return {
        "version": str(payload.get("version") or settings.version),
        "display_version": settings.display_version,
        "build_id": str(payload.get("build_id") or fallback["build_id"]),
        "source_fingerprint": str(payload.get("source_fingerprint") or "unknown"),
        "channel": str(payload.get("channel") or "stable"),
        "built_at": payload.get("built_at"),
    }


def _schema_version(connection) -> int:
    row = connection.execute(
        "SELECT version FROM schema_version WHERE singleton_id = 1"
    ).fetchone()
    return int(row[0] if row else 0)


def record_release_event(
    *,
    event_type: str,
    status: str,
    previous_version: str | None = None,
    detail: dict | None = None,
    log_path: str | None = None,
    version: str | None = None,
    build_id: str | None = None,
    source_fingerprint: str | None = None,
) -> dict:
    manifest = load_release_manifest()
    now = utc_now()
    with database() as connection:
        schema_version = _schema_version(connection)
        cursor = connection.execute(
            """
            INSERT INTO system_upgrade_history (
                version, build_id, source_fingerprint, previous_version,
                event_type, status, schema_version, log_path, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(version or manifest["version"]),
                str(build_id or manifest["build_id"]),
                str(source_fingerprint or manifest["source_fingerprint"]),
                previous_version,
                str(event_type).strip().upper(),
                str(status).strip().upper(),
                schema_version,
                log_path,
                json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
        event_id = int(cursor.lastrowid)
    return get_release_event(event_id) or {"id": event_id}


def record_application_start() -> dict:
    manifest = load_release_manifest()
    with database() as connection:
        existing = connection.execute(
            """
            SELECT id FROM system_upgrade_history
            WHERE event_type = 'APP_STARTED' AND build_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (manifest["build_id"],),
        ).fetchone()
    if existing:
        return get_release_event(int(existing["id"])) or {"id": int(existing["id"])}
    return record_release_event(
        event_type="APP_STARTED",
        status="SUCCESS",
        detail={"message": "应用启动并完成数据库迁移"},
    )


def get_release_event(event_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM system_upgrade_history WHERE id = ?",
            (int(event_id),),
        ).fetchone()
    if not row:
        return None
    payload = dict(row)
    try:
        payload["detail"] = json.loads(payload.pop("detail_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        payload["detail"] = {}
    return payload


def list_release_history(limit: int = 30) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with database() as connection:
        rows = connection.execute(
            "SELECT * FROM system_upgrade_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result: list[dict] = []
    for row in rows:
        payload = dict(row)
        try:
            payload["detail"] = json.loads(payload.pop("detail_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload["detail"] = {}
        result.append(payload)
    return result


def current_release_info() -> dict:
    manifest = load_release_manifest()
    with database() as connection:
        schema_version = _schema_version(connection)
        latest_upgrade = connection.execute(
            """
            SELECT * FROM system_upgrade_history
            WHERE event_type IN ('UPGRADE_COMPLETED','UPGRADE_FAILED','APP_STARTED')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    payload = dict(manifest)
    payload["schema_version"] = schema_version
    payload["latest_event"] = None
    if latest_upgrade:
        event = dict(latest_upgrade)
        try:
            event["detail"] = json.loads(event.pop("detail_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            event["detail"] = {}
        payload["latest_event"] = event
    return payload
