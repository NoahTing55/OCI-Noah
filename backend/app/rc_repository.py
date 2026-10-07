import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from .database import database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_resource_snapshot(
    *,
    account_id: int,
    resource_type: str,
    resource_id: str,
    region: str | None,
    payload: dict | list,
) -> dict:
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO oci_resource_cache (
                account_id, resource_type, resource_id, region, payload_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, resource_type, resource_id)
            DO UPDATE SET
                region = excluded.region,
                payload_json = excluded.payload_json,
                synced_at = excluded.synced_at
            """,
            (
                int(account_id),
                str(resource_type).upper(),
                str(resource_id),
                region,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
    return {
        "account_id": int(account_id),
        "resource_type": str(resource_type).upper(),
        "resource_id": str(resource_id),
        "region": region,
        "payload": payload,
        "synced_at": now,
    }


def replace_resource_type_cache(
    *,
    account_id: int,
    resource_type: str,
    region: str | None,
    items: list[dict],
    id_field: str = "id",
) -> dict:
    normalized_type = str(resource_type).upper()
    now = utc_now()
    rows: list[tuple] = []
    for index, item in enumerate(items):
        resource_id = str(item.get(id_field) or item.get("name") or index)
        rows.append(
            (
                int(account_id),
                normalized_type,
                resource_id,
                region,
                json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                now,
            )
        )
    with database() as connection:
        if region:
            connection.execute(
                """
                DELETE FROM oci_resource_cache
                WHERE account_id = ? AND resource_type = ? AND region = ?
                """,
                (int(account_id), normalized_type, region),
            )
        else:
            connection.execute(
                """
                DELETE FROM oci_resource_cache
                WHERE account_id = ? AND resource_type = ?
                """,
                (int(account_id), normalized_type),
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO oci_resource_cache (
                    account_id, resource_type, resource_id, region, payload_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
    return {"count": len(items), "synced_at": now}


def list_resource_cache(
    account_id: int,
    resource_type: str,
    *,
    region: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    params: list[Any] = [int(account_id), str(resource_type).upper()]
    where = "account_id = ? AND resource_type = ?"
    if region:
        where += " AND region = ?"
        params.append(region)
    params.append(max(1, min(int(limit), 5000)))
    with database() as connection:
        rows = connection.execute(
            f"""
            SELECT account_id, resource_type, resource_id, region,
                   payload_json, synced_at
            FROM oci_resource_cache
            WHERE {where}
            ORDER BY synced_at DESC, resource_id
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except (TypeError, json.JSONDecodeError):
            item["payload"] = None
            item.pop("payload_json", None)
        result.append(item)
    return result


def get_resource_cache(
    account_id: int,
    resource_type: str,
    resource_id: str,
) -> dict | None:
    with database() as connection:
        row = connection.execute(
            """
            SELECT account_id, resource_type, resource_id, region,
                   payload_json, synced_at
            FROM oci_resource_cache
            WHERE account_id = ? AND resource_type = ? AND resource_id = ?
            """,
            (int(account_id), str(resource_type).upper(), str(resource_id)),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["payload"] = json.loads(item.pop("payload_json"))
    except (TypeError, json.JSONDecodeError):
        return None
    return item


def delete_resource_cache(
    account_id: int,
    resource_type: str,
    resource_id: str,
) -> None:
    with database() as connection:
        connection.execute(
            """
            DELETE FROM oci_resource_cache
            WHERE account_id = ? AND resource_type = ? AND resource_id = ?
            """,
            (int(account_id), str(resource_type).upper(), str(resource_id)),
        )


def add_ip_quality_history(
    *,
    account_id: int,
    instance_id: str,
    private_ip_id: str,
    public_ip: str | None,
    region: str,
    score: int | None,
    payload: dict,
    action: str,
) -> dict:
    now = utc_now()
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO ip_quality_history (
                account_id, instance_id, private_ip_id, public_ip, region,
                score, action, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(account_id),
                instance_id,
                private_ip_id,
                public_ip,
                region,
                score,
                action,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
        record_id = int(cursor.lastrowid)
    return {
        "id": record_id,
        "account_id": int(account_id),
        "instance_id": instance_id,
        "private_ip_id": private_ip_id,
        "public_ip": public_ip,
        "region": region,
        "score": score,
        "action": action,
        "payload": payload,
        "created_at": now,
    }


def list_ip_quality_history(
    account_id: int,
    *,
    instance_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    params: list[Any] = [int(account_id)]
    where = "account_id = ?"
    if instance_id:
        where += " AND instance_id = ?"
        params.append(instance_id)
    params.append(max(1, min(int(limit), 1000)))
    with database() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM ip_quality_history
            WHERE {where}
            ORDER BY id DESC LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except (TypeError, json.JSONDecodeError):
            item["payload"] = {}
            item.pop("payload_json", None)
        result.append(item)
    return result


def create_vnc_session_record(
    *,
    account_id: int,
    instance_id: str,
    region: str,
    connection_id: str,
    local_port: int,
    expires_at: str,
) -> tuple[dict, str]:
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = utc_now()
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO vnc_sessions (
                account_id, instance_id, region, console_connection_id,
                token_hash, local_port, status, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'STARTING', ?, ?)
            """,
            (
                int(account_id),
                instance_id,
                region,
                connection_id,
                token_hash,
                int(local_port),
                now,
                expires_at,
            ),
        )
        session_id = int(cursor.lastrowid)
    return get_vnc_session(session_id) or {"id": session_id}, token


def get_vnc_session(session_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM vnc_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
    return dict(row) if row else None


def get_vnc_session_by_token(token: str) -> dict | None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = utc_now()
    with database() as connection:
        row = connection.execute(
            """
            SELECT * FROM vnc_sessions
            WHERE token_hash = ? AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
    return dict(row) if row else None


def list_vnc_sessions(account_id: int, instance_id: str | None = None) -> list[dict]:
    params: list[Any] = [int(account_id)]
    where = "account_id = ?"
    if instance_id:
        where += " AND instance_id = ?"
        params.append(instance_id)
    with database() as connection:
        rows = connection.execute(
            f"SELECT * FROM vnc_sessions WHERE {where} ORDER BY id DESC LIMIT 100",
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def update_vnc_session(
    session_id: int,
    *,
    status: str,
    error: str | None = None,
    stopped_at: str | None = None,
) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE vnc_sessions
            SET status = ?, error = ?, stopped_at = COALESCE(?, stopped_at)
            WHERE id = ?
            """,
            (status, error[:2000] if error else None, stopped_at, int(session_id)),
        )


def create_multipart_session(
    *,
    account_id: int,
    region: str,
    namespace: str,
    bucket_name: str,
    object_name: str,
    upload_id: str,
) -> dict:
    now = utc_now()
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO object_multipart_sessions (
                account_id, region, namespace, bucket_name, object_name,
                upload_id, status, parts_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', '[]', ?, ?)
            """,
            (
                int(account_id),
                region,
                namespace,
                bucket_name,
                object_name,
                upload_id,
                now,
                now,
            ),
        )
        session_id = int(cursor.lastrowid)
    return get_multipart_session(session_id) or {"id": session_id}


def get_multipart_session(session_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM object_multipart_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["parts"] = json.loads(item.pop("parts_json"))
    except (TypeError, json.JSONDecodeError):
        item["parts"] = []
        item.pop("parts_json", None)
    return item


def list_multipart_sessions(account_id: int, *, active_only: bool = False) -> list[dict]:
    query = "SELECT id FROM object_multipart_sessions WHERE account_id = ?"
    params: list[Any] = [int(account_id)]
    if active_only:
        query += " AND status = 'ACTIVE'"
    query += " ORDER BY id DESC LIMIT 100"
    with database() as connection:
        ids = [row["id"] for row in connection.execute(query, tuple(params)).fetchall()]
    return [item for item in (get_multipart_session(item_id) for item_id in ids) if item]


def save_multipart_part(session_id: int, part_number: int, etag: str) -> dict:
    session = get_multipart_session(session_id)
    if not session:
        raise KeyError("分片上传会话不存在")
    parts = [item for item in session["parts"] if int(item["part_number"]) != int(part_number)]
    parts.append({"part_number": int(part_number), "etag": etag})
    parts.sort(key=lambda item: int(item["part_number"]))
    with database() as connection:
        connection.execute(
            """
            UPDATE object_multipart_sessions
            SET parts_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(parts, ensure_ascii=False, separators=(",", ":")),
                utc_now(),
                int(session_id),
            ),
        )
    return get_multipart_session(session_id) or session


def finish_multipart_session(session_id: int, status: str) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE object_multipart_sessions
            SET status = ?, updated_at = ? WHERE id = ?
            """,
            (status, utc_now(), int(session_id)),
        )


def create_launch_profile(
    *,
    account_id: int,
    name: str,
    payload: dict,
) -> dict:
    now = utc_now()
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO launch_profiles (
                account_id, name, payload_json, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?)
            """,
            (
                int(account_id),
                name.strip(),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )
        profile_id = int(cursor.lastrowid)
    return get_launch_profile(profile_id) or {"id": profile_id}


def get_launch_profile(profile_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM launch_profiles WHERE id = ?",
            (int(profile_id),),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["payload"] = json.loads(item.pop("payload_json"))
    except (TypeError, json.JSONDecodeError):
        item["payload"] = {}
        item.pop("payload_json", None)
    item["enabled"] = bool(item["enabled"])
    return item


def list_launch_profiles(account_id: int) -> list[dict]:
    with database() as connection:
        ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM launch_profiles WHERE account_id = ? ORDER BY id DESC",
                (int(account_id),),
            ).fetchall()
        ]
    return [item for item in (get_launch_profile(item_id) for item_id in ids) if item]


def update_launch_profile(
    profile_id: int,
    *,
    name: str | None = None,
    payload: dict | None = None,
    enabled: bool | None = None,
) -> dict:
    existing = get_launch_profile(profile_id)
    if not existing:
        raise KeyError("抢机配置不存在")
    final_name = name.strip() if name is not None else existing["name"]
    if not final_name:
        raise ValueError("配置名称不能为空")
    final_payload = payload if payload is not None else existing["payload"]
    final_enabled = bool(enabled) if enabled is not None else existing["enabled"]
    with database() as connection:
        connection.execute(
            """
            UPDATE launch_profiles
            SET name = ?, payload_json = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                final_name,
                json.dumps(final_payload, ensure_ascii=False, separators=(",", ":")),
                1 if final_enabled else 0,
                utc_now(),
                int(profile_id),
            ),
        )
    return get_launch_profile(profile_id) or existing


def delete_launch_profile(profile_id: int) -> None:
    with database() as connection:
        connection.execute("DELETE FROM launch_profiles WHERE id = ?", (int(profile_id),))


def set_launch_profiles_enabled(account_id: int, enabled: bool) -> int:
    with database() as connection:
        cursor = connection.execute(
            "UPDATE launch_profiles SET enabled = ?, updated_at = ? WHERE account_id = ?",
            (1 if enabled else 0, utc_now(), int(account_id)),
        )
        return int(cursor.rowcount)
