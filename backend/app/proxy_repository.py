import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlsplit

from cryptography.fernet import InvalidToken

from .credential_crypto import decrypt_secret, encrypt_secret
from .database import database
from .proxy_service import (
    ProxyConfigurationError,
    mask_proxy_url,
    normalize_proxy_url,
    normalize_rotation_config,
)


_UNSET = object()


class ProxyAssignmentConflictError(RuntimeError):
    pass

PUBLIC_COLUMNS = """
    p.id,
    p.name,
    p.scheme,
    p.host,
    p.port,
    p.has_auth,
    CASE WHEN p.rotate_api_url_encrypted IS NOT NULL THEN 1 ELSE 0 END AS has_rotate_api,
    p.rotate_api_method,
    p.rotate_wait_seconds,
    p.last_rotate_at,
    p.last_rotate_error,
    p.last_ip,
    p.last_test_at,
    p.last_error,
    p.last_latency_ms,
    p.last_country_code,
    p.last_country_name,
    p.last_region_name,
    p.last_city,
    p.last_success_at,
    p.consecutive_failures,
    p.is_enabled,
    p.paused_until,
    p.created_at,
    p.updated_at,
    (SELECT COUNT(*) FROM oci_accounts a WHERE a.proxy_profile_id = p.id) AS assigned_count,
    (SELECT a.id FROM oci_accounts a WHERE a.proxy_profile_id = p.id LIMIT 1) AS assigned_account_id,
    (SELECT a.custom_name FROM oci_accounts a WHERE a.proxy_profile_id = p.id LIMIT 1) AS assigned_account_name
"""


def _parts(proxy_url: str) -> tuple[str, str, int, bool]:
    parsed = urlsplit(proxy_url)
    return (
        parsed.scheme.lower(),
        parsed.hostname or "",
        int(parsed.port or 0),
        bool(parsed.username),
    )


def _row_dict(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    item = dict(row)
    item["has_auth"] = bool(item.get("has_auth"))
    item["has_rotate_api"] = bool(item.get("has_rotate_api"))
    item["assigned_count"] = int(item.get("assigned_count") or 0)
    item["is_enabled"] = bool(item.get("is_enabled", 1))
    item["rotate_wait_seconds"] = int(item.get("rotate_wait_seconds") or 3)
    item["rotate_api_method"] = str(item.get("rotate_api_method") or "GET")
    item["consecutive_failures"] = int(item.get("consecutive_failures") or 0)
    if item.get("last_latency_ms") is not None:
        item["last_latency_ms"] = int(item["last_latency_ms"])
    return item


def _normalize_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError("代理名称不能为空")
    if len(value) > 120:
        raise ValueError("代理名称不能超过 120 个字符")
    return value


def _rotation_db_values(config: dict | None) -> tuple:
    if config is None:
        return (None, None, "GET", None, None, 3)
    normalized = normalize_rotation_config(
        api_url=config.get("api_url", ""),
        method=config.get("method"),
        headers=config.get("headers"),
        body=config.get("body"),
        wait_seconds=config.get("wait_seconds"),
    )
    headers_json = json.dumps(normalized["headers"], ensure_ascii=False, separators=(",", ":"))
    return (
        encrypt_secret(normalized["api_url"]),
        encrypt_secret(headers_json) if normalized["headers"] else None,
        normalized["method"],
        encrypt_secret(normalized["body"]) if normalized["body"] is not None else None,
        None,
        normalized["wait_seconds"],
    )


def unique_proxy_name(base_name: str) -> str:
    base = _normalize_name(base_name)
    with database() as connection:
        rows = connection.execute("SELECT name FROM proxy_profiles").fetchall()
    existing = {str(row["name"]).casefold() for row in rows}
    if base.casefold() not in existing:
        return base
    number = 2
    while f"{base}-{number}".casefold() in existing:
        number += 1
    return f"{base}-{number}"


def list_proxy_profiles() -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM proxy_profiles p ORDER BY p.id DESC"
        ).fetchall()
    return [_row_dict(row) for row in rows]


def get_proxy_profile(profile_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM proxy_profiles p WHERE p.id = ?",
            (int(profile_id),),
        ).fetchone()
    return _row_dict(row)


def get_proxy_profile_with_secret(profile_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM proxy_profiles WHERE id = ?",
            (int(profile_id),),
        ).fetchone()
    return dict(row) if row else None


def find_proxy_profile_by_url(proxy_url: str) -> dict | None:
    normalized = normalize_proxy_url(proxy_url)
    with database() as connection:
        rows = connection.execute(
            "SELECT id, proxy_url_encrypted FROM proxy_profiles ORDER BY id"
        ).fetchall()
    for row in rows:
        try:
            current = normalize_proxy_url(decrypt_secret(row["proxy_url_encrypted"]))
        except (InvalidToken, ValueError):
            continue
        if current == normalized:
            return get_proxy_profile(int(row["id"]))
    return None


def create_proxy_profile(
    *,
    name: str,
    proxy_url: str,
    rotation_config: dict | None = None,
) -> dict:
    normalized_name = _normalize_name(name)
    normalized_url = normalize_proxy_url(proxy_url)
    scheme, host, port, has_auth = _parts(normalized_url)
    rotate_url, rotate_headers, rotate_method, rotate_body, _, rotate_wait = _rotation_db_values(rotation_config)
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO proxy_profiles (
                name, proxy_url_encrypted, scheme, host, port, has_auth,
                rotate_api_url_encrypted, rotate_api_headers_encrypted,
                rotate_api_method, rotate_api_body_encrypted, rotate_wait_seconds,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                normalized_name,
                encrypt_secret(normalized_url),
                scheme,
                host,
                port,
                1 if has_auth else 0,
                rotate_url,
                rotate_headers,
                rotate_method,
                rotate_body,
                rotate_wait,
            ),
        )
        profile_id = int(cursor.lastrowid)
    profile = get_proxy_profile(profile_id)
    if not profile:
        raise RuntimeError("代理保存后无法读取")
    return profile


def update_proxy_profile(
    profile_id: int,
    *,
    name: str | None = None,
    proxy_url: str | None = None,
    rotation_config: dict | None | object = _UNSET,
    is_enabled: bool | object = _UNSET,
) -> dict | None:
    current = get_proxy_profile_with_secret(profile_id)
    if not current:
        return None
    if is_enabled is not _UNSET and not bool(is_enabled):
        public = get_proxy_profile(profile_id)
        if public and int(public.get("assigned_count") or 0) > 0:
            raise ProxyAssignmentConflictError("该代理仍绑定租户，请先解除绑定后再停用")
    updates: list[str] = []
    params: list[object] = []
    if name is not None:
        updates.append("name = ?")
        params.append(_normalize_name(name))
    if proxy_url is not None:
        normalized_url = normalize_proxy_url(proxy_url)
        scheme, host, port, has_auth = _parts(normalized_url)
        updates.extend(
            [
                "proxy_url_encrypted = ?",
                "scheme = ?",
                "host = ?",
                "port = ?",
                "has_auth = ?",
                "last_ip = NULL",
                "last_test_at = NULL",
                "last_error = NULL",
            ]
        )
        params.extend(
            [
                encrypt_secret(normalized_url),
                scheme,
                host,
                port,
                1 if has_auth else 0,
            ]
        )
    if rotation_config is not _UNSET:
        rotate_url, rotate_headers, rotate_method, rotate_body, _, rotate_wait = _rotation_db_values(rotation_config)
        updates.extend(
            [
                "rotate_api_url_encrypted = ?",
                "rotate_api_headers_encrypted = ?",
                "rotate_api_method = ?",
                "rotate_api_body_encrypted = ?",
                "rotate_wait_seconds = ?",
                "last_rotate_at = NULL",
                "last_rotate_error = NULL",
            ]
        )
        params.extend(
            [rotate_url, rotate_headers, rotate_method, rotate_body, rotate_wait]
        )
    if is_enabled is not _UNSET:
        updates.append("is_enabled = ?")
        params.append(1 if bool(is_enabled) else 0)
    if not updates:
        return get_proxy_profile(profile_id)
    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(int(profile_id))
    with database() as connection:
        connection.execute(
            f"UPDATE proxy_profiles SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        refreshed = connection.execute(
            "SELECT name, proxy_url_encrypted, last_ip, last_test_at, last_error FROM proxy_profiles WHERE id = ?",
            (int(profile_id),),
        ).fetchone()
        if refreshed:
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_url_encrypted = ?,
                    proxy_label = ?,
                    proxy_last_ip = ?,
                    proxy_last_test_at = ?,
                    proxy_last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE proxy_profile_id = ?
                """,
                (
                    refreshed["proxy_url_encrypted"],
                    refreshed["name"],
                    refreshed["last_ip"],
                    refreshed["last_test_at"],
                    refreshed["last_error"],
                    int(profile_id),
                ),
            )
    return get_proxy_profile(profile_id)


def delete_proxy_profile(profile_id: int) -> tuple[bool, int]:
    with database() as connection:
        assigned = connection.execute(
            "SELECT COUNT(*) AS total FROM oci_accounts WHERE proxy_profile_id = ?",
            (int(profile_id),),
        ).fetchone()
        detached = int(assigned["total"] or 0)
        connection.execute(
            """
            UPDATE oci_accounts
            SET proxy_enabled = 0,
                proxy_profile_id = NULL,
                proxy_url_encrypted = NULL,
                proxy_label = NULL,
                proxy_last_ip = NULL,
                proxy_last_test_at = NULL,
                proxy_last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE proxy_profile_id = ?
            """,
            (int(profile_id),),
        )
        cursor = connection.execute(
            "DELETE FROM proxy_profiles WHERE id = ?",
            (int(profile_id),),
        )
    return cursor.rowcount > 0, detached


def bind_account_proxy(
    *, account_id: int, profile_id: int | None, enabled: bool
) -> dict | None:
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = connection.execute(
            "SELECT id, proxy_profile_id FROM oci_accounts WHERE id = ?",
            (int(account_id),),
        ).fetchone()
        if not account:
            return None
        profile = None
        if profile_id is not None:
            profile = connection.execute(
                "SELECT * FROM proxy_profiles WHERE id = ?",
                (int(profile_id),),
            ).fetchone()
            if not profile:
                raise KeyError("代理不存在")
            if not bool(profile["is_enabled"]):
                raise ProxyAssignmentConflictError("该代理已停用，不能分配给租户")
            owner = connection.execute(
                "SELECT id, custom_name FROM oci_accounts WHERE proxy_profile_id = ? AND id <> ? LIMIT 1",
                (int(profile_id), int(account_id)),
            ).fetchone()
            if owner:
                raise ProxyAssignmentConflictError(
                    f"该代理已分配给租户“{owner['custom_name']}”，请选择未分配代理"
                )
        if profile:
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_profile_id = ?,
                    proxy_enabled = ?,
                    proxy_url_encrypted = ?,
                    proxy_label = ?,
                    proxy_last_ip = ?,
                    proxy_last_test_at = ?,
                    proxy_last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(profile_id),
                    1 if enabled else 0,
                    profile["proxy_url_encrypted"],
                    profile["name"],
                    profile["last_ip"],
                    profile["last_test_at"],
                    profile["last_error"],
                    int(account_id),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_profile_id = NULL,
                    proxy_enabled = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (1 if enabled else 0, int(account_id)),
            )
        row = connection.execute(
            "SELECT id FROM oci_accounts WHERE id = ?", (int(account_id),)
        ).fetchone()
    return {"id": int(account_id)} if row else None


def list_assignable_proxy_profiles(account_id: int | None = None) -> list[dict]:
    current_id = None
    with database() as connection:
        if account_id is not None:
            row = connection.execute(
                "SELECT proxy_profile_id FROM oci_accounts WHERE id = ?", (int(account_id),)
            ).fetchone()
            if not row:
                raise KeyError("OCI 账户不存在")
            current_id = row["proxy_profile_id"]
        rows = connection.execute(
            f"""
            SELECT {PUBLIC_COLUMNS}
            FROM proxy_profiles p
            WHERE p.is_enabled = 1
              AND (
                    NOT EXISTS (SELECT 1 FROM oci_accounts a WHERE a.proxy_profile_id = p.id)
                    OR p.id = ?
                  )
            ORDER BY
              CASE WHEN p.id = ? THEN 0 ELSE 1 END,
              CASE WHEN p.last_error IS NULL AND p.last_success_at IS NOT NULL THEN 0 ELSE 1 END,
              COALESCE(p.last_latency_ms, 2147483647), p.id
            """,
            (current_id, current_id),
        ).fetchall()
    return [_row_dict(row) for row in rows]


def _allocation_candidates(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT p.*
        FROM proxy_profiles p
        WHERE p.is_enabled = 1
          AND p.last_success_at IS NOT NULL
          AND p.last_error IS NULL
          AND COALESCE(p.consecutive_failures, 0) = 0
          AND (p.paused_until IS NULL OR datetime(p.paused_until) <= datetime('now'))
          AND NOT EXISTS (
              SELECT 1 FROM oci_accounts a WHERE a.proxy_profile_id = p.id
          )
        ORDER BY COALESCE(p.last_latency_ms, 2147483647), p.last_success_at DESC, p.id
        """
    ).fetchall()


def proxy_allocation_preview(account_ids: list[int] | None = None, *, reassign: bool = False) -> dict:
    with database() as connection:
        params: list[object] = []
        where = "1=1"
        if account_ids:
            normalized = sorted({int(value) for value in account_ids})
            where = "id IN (" + ",".join("?" for _ in normalized) + ")"
            params.extend(normalized)
        if not reassign:
            where += " AND proxy_profile_id IS NULL"
        accounts = connection.execute(
            f"SELECT id, custom_name, proxy_profile_id FROM oci_accounts WHERE {where} ORDER BY id",
            tuple(params),
        ).fetchall()
        candidates = _allocation_candidates(connection)
    assignable = min(len(accounts), len(candidates))
    return {
        "tenant_count": len(accounts),
        "available_proxy_count": len(candidates),
        "assignable_count": assignable,
        "unassigned_count": max(0, len(accounts) - assignable),
        "reassign": bool(reassign),
        "accounts": [{"id": int(row["id"]), "name": row["custom_name"]} for row in accounts],
    }


def auto_allocate_proxies(account_ids: list[int] | None = None, *, reassign: bool = False) -> dict:
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        params: list[object] = []
        where = "1=1"
        if account_ids:
            normalized = sorted({int(value) for value in account_ids})
            where = "id IN (" + ",".join("?" for _ in normalized) + ")"
            params.extend(normalized)
        if not reassign:
            where += " AND proxy_profile_id IS NULL"
        accounts = connection.execute(
            f"SELECT id, custom_name, proxy_profile_id FROM oci_accounts WHERE {where} ORDER BY id",
            tuple(params),
        ).fetchall()
        candidates = list(_allocation_candidates(connection))
        # Randomize within similar latency bands to avoid a stable account/proxy signature.
        random.shuffle(candidates)
        candidates.sort(key=lambda row: (int(row["last_latency_ms"] or 2147483647) // 25, -int(row["id"])))
        results = []
        for account, profile in zip(accounts, candidates):
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_profile_id = ?, proxy_enabled = 1,
                    proxy_url_encrypted = ?, proxy_label = ?,
                    proxy_last_ip = ?, proxy_last_test_at = ?, proxy_last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(profile["id"]), profile["proxy_url_encrypted"], profile["name"],
                    profile["last_ip"], profile["last_test_at"], profile["last_error"],
                    int(account["id"]),
                ),
            )
            results.append({
                "account_id": int(account["id"]), "account_name": account["custom_name"],
                "proxy_id": int(profile["id"]), "proxy_name": profile["name"], "status": "ASSIGNED",
            })
        assigned_ids = {int(item["account_id"]) for item in results}
        for account in accounts:
            if int(account["id"]) not in assigned_ids:
                results.append({
                    "account_id": int(account["id"]), "account_name": account["custom_name"],
                    "proxy_id": None, "proxy_name": None, "status": "NO_AVAILABLE_PROXY",
                })
    assigned = sum(1 for item in results if item["status"] == "ASSIGNED")
    return {
        "total": len(results), "assigned": assigned,
        "unassigned": len(results) - assigned, "reassign": bool(reassign), "items": results,
    }


def save_proxy_profile_test_result(
    profile_id: int,
    *,
    ip_address: str | None,
    tested_at: str,
    error: str | None,
    latency_ms: int | None = None,
    country_code: str | None = None,
    country_name: str | None = None,
    region_name: str | None = None,
    city: str | None = None,
    task_id: int | None = None,
) -> None:
    success = error is None and bool(ip_address)
    with database() as connection:
        if success:
            connection.execute(
                """
                UPDATE proxy_profiles
                SET last_ip = ?, last_test_at = ?, last_error = NULL,
                    last_latency_ms = ?, last_country_code = ?,
                    last_country_name = ?, last_region_name = ?, last_city = ?,
                    last_success_at = ?, consecutive_failures = 0, paused_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    ip_address, tested_at, latency_ms, country_code, country_name,
                    region_name, city, tested_at, int(profile_id),
                ),
            )
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_last_ip = ?, proxy_last_test_at = ?, proxy_last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE proxy_profile_id = ?
                """,
                (ip_address, tested_at, int(profile_id)),
            )
        else:
            connection.execute(
                """
                UPDATE proxy_profiles
                SET last_test_at = ?, last_error = ?,
                    consecutive_failures = consecutive_failures + 1,
                    paused_until = datetime('now', '+120 seconds'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (tested_at, str(error or "代理测试失败")[:2000], int(profile_id)),
            )
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_last_test_at = ?, proxy_last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE proxy_profile_id = ?
                """,
                (tested_at, str(error or "代理测试失败")[:2000], int(profile_id)),
            )
        connection.execute(
            """
            INSERT INTO proxy_health_history (
                proxy_profile_id, task_id, success, exit_ip, latency_ms,
                country_code, country_name, region_name, city, error, tested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(profile_id), task_id, 1 if success else 0, ip_address,
                latency_ms, country_code, country_name, region_name, city,
                str(error)[:2000] if error else None, tested_at,
            ),
        )

def save_proxy_profile_rotate_result(
    profile_id: int,
    *,
    rotated_at: str,
    error: str | None,
    old_ip: str | None = None,
    new_ip: str | None = None,
    changed: bool = False,
    http_status: int | None = None,
) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE proxy_profiles
            SET last_rotate_at = ?, last_rotate_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (rotated_at, error, int(profile_id)),
        )
        connection.execute(
            """
            INSERT INTO proxy_rotation_history (
                proxy_profile_id, success, old_ip, new_ip, changed,
                http_status, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(profile_id), 0 if error else 1, old_ip, new_ip,
                1 if changed else 0, http_status,
                str(error)[:2000] if error else None, rotated_at,
            ),
        )


def list_proxy_health_history(profile_id: int, limit: int = 30) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with database() as connection:
        rows = connection.execute(
            """
            SELECT id, proxy_profile_id, task_id, success, exit_ip, latency_ms,
                   country_code, country_name, region_name, city, error, tested_at
            FROM proxy_health_history
            WHERE proxy_profile_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (int(profile_id), limit),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["success"] = bool(item.get("success"))
        result.append(item)
    return result


def list_proxy_rotation_history(profile_id: int, limit: int = 30) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with database() as connection:
        rows = connection.execute(
            """
            SELECT id, proxy_profile_id, success, old_ip, new_ip, changed,
                   http_status, error, created_at
            FROM proxy_rotation_history
            WHERE proxy_profile_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (int(profile_id), limit),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["success"] = bool(item.get("success"))
        item["changed"] = bool(item.get("changed"))
        result.append(item)
    return result

def decrypt_proxy_profile_url(profile_id: int) -> str:
    profile = get_proxy_profile_with_secret(profile_id)
    if not profile:
        raise KeyError("代理不存在")
    return decrypt_secret(profile["proxy_url_encrypted"])


def get_proxy_rotation_config(profile_id: int) -> dict | None:
    profile = get_proxy_profile_with_secret(profile_id)
    if not profile:
        raise KeyError("代理不存在")
    encrypted_url = profile.get("rotate_api_url_encrypted")
    if not encrypted_url:
        return None
    api_url = decrypt_secret(encrypted_url)
    headers: dict[str, str] = {}
    encrypted_headers = profile.get("rotate_api_headers_encrypted")
    if encrypted_headers:
        loaded = json.loads(decrypt_secret(encrypted_headers))
        if isinstance(loaded, dict):
            headers = {str(key): str(value) for key, value in loaded.items()}
    body = None
    if profile.get("rotate_api_body_encrypted"):
        body = decrypt_secret(profile["rotate_api_body_encrypted"])
    return normalize_rotation_config(
        api_url=api_url,
        method=profile.get("rotate_api_method"),
        headers=headers,
        body=body,
        wait_seconds=profile.get("rotate_wait_seconds"),
    )


def migrate_legacy_account_proxies() -> int:
    """Move old per-account encrypted proxies into the global proxy library."""
    migrated = 0
    with database() as connection:
        accounts = connection.execute(
            """
            SELECT id, custom_name, proxy_label, proxy_url_encrypted,
                   proxy_last_ip, proxy_last_test_at, proxy_last_error
            FROM oci_accounts
            WHERE proxy_profile_id IS NULL AND proxy_url_encrypted IS NOT NULL
            ORDER BY id
            """
        ).fetchall()
    for row in accounts:
        try:
            decrypted = decrypt_secret(row["proxy_url_encrypted"])
            normalized = normalize_proxy_url(decrypted)
        except (InvalidToken, ValueError):
            continue
        base_name = row["proxy_label"] or f"{row['custom_name']}代理"
        profile_name = unique_proxy_name(base_name)
        try:
            profile = create_proxy_profile(name=profile_name, proxy_url=normalized)
        except sqlite3.IntegrityError:
            profile_name = unique_proxy_name(profile_name)
            profile = create_proxy_profile(name=profile_name, proxy_url=normalized)
        with database() as connection:
            connection.execute(
                """
                UPDATE proxy_profiles
                SET last_ip = ?, last_test_at = ?, last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    row["proxy_last_ip"],
                    row["proxy_last_test_at"],
                    row["proxy_last_error"],
                    profile["id"],
                ),
            )
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_profile_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (profile["id"], row["id"]),
            )
        migrated += 1
    return migrated


def public_profile_with_display_url(profile_id: int) -> dict | None:
    profile = get_proxy_profile(profile_id)
    if not profile:
        return None
    secret = get_proxy_profile_with_secret(profile_id)
    try:
        profile["display_url"] = mask_proxy_url(
            decrypt_secret(secret["proxy_url_encrypted"])
        )
    except InvalidToken:
        profile["display_url"] = "代理凭据无法解密"
    return profile


def public_profile_for_edit(profile_id: int) -> dict | None:
    """Return editable non-secret proxy fields without exposing the password."""
    profile = public_profile_with_display_url(profile_id)
    if not profile:
        return None
    secret = get_proxy_profile_with_secret(profile_id)
    try:
        parsed = urlsplit(decrypt_secret(secret["proxy_url_encrypted"]))
        profile["username"] = unquote(parsed.username or "") or None
    except InvalidToken:
        profile["username"] = None
    return profile


def build_proxy_url_update(profile_id: int, updates: dict[str, object]) -> str:
    """Merge structured editor values with the encrypted current proxy URL.

    An empty password preserves the existing password. Authentication is only
    removed when clear_auth is explicitly true.
    """
    current_url = decrypt_proxy_profile_url(profile_id)
    parsed = urlsplit(current_url)

    scheme = str(updates.get("scheme", parsed.scheme) or "").strip().lower()
    host = str(updates.get("host", parsed.hostname or "") or "").strip().strip("[]")
    port_value = updates.get("port", parsed.port)
    try:
        port = int(port_value or 0)
    except (TypeError, ValueError) as exc:
        raise ProxyConfigurationError("端口必须是数字") from exc
    if not host:
        raise ProxyConfigurationError("请填写服务器地址")
    if port < 1 or port > 65535:
        raise ProxyConfigurationError("端口必须在 1–65535 之间")

    current_username = unquote(parsed.username or "")
    current_password = unquote(parsed.password or "")
    if bool(updates.get("clear_auth")):
        username = ""
        password = ""
    else:
        username = (
            str(updates.get("username") or "").strip()
            if "username" in updates
            else current_username
        )
        submitted_password = updates.get("password") if "password" in updates else None
        password = (
            str(submitted_password)
            if submitted_password not in (None, "")
            else current_password
        )
        if not username and (current_username or current_password):
            raise ProxyConfigurationError("如需清除代理认证，请勾选“清除用户名和密码”")
        if password and not username:
            raise ProxyConfigurationError("填写密码时必须同时填写用户名")

    normalized_host = f"[{host}]" if ":" in host else host
    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth += f":{quote(password, safe='')}"
        auth += "@"
    return normalize_proxy_url(f"{scheme}://{auth}{normalized_host}:{port}")
