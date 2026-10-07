import json
from datetime import datetime, timezone

from .database import database


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def replace_account_instance_cache(
    *,
    account_id: int,
    account_name: str,
    account_email: str | None,
    instances: list[dict],
    errors: list[dict],
    regions_scanned: int,
) -> None:
    synced_at = _utc_now()

    with database() as connection:
        connection.execute(
            "DELETE FROM oci_instance_cache WHERE account_id = ?",
            (account_id,),
        )

        for instance in instances:
            payload = dict(instance)
            instance_id = str(payload.get("id") or "").strip()
            if not instance_id:
                continue
            payload["account_id"] = account_id
            payload["account_name"] = account_name
            payload["account_email"] = account_email
            connection.execute(
                """
                INSERT INTO oci_instance_cache (
                    account_id,
                    instance_id,
                    region,
                    payload_json,
                    synced_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    instance_id,
                    str(payload.get("region") or ""),
                    json.dumps(payload, ensure_ascii=False),
                    synced_at,
                ),
            )

        connection.execute(
            """
            INSERT INTO oci_instance_cache_syncs (
                account_id,
                regions_scanned,
                errors_json,
                last_error,
                synced_at
            )
            VALUES (?, ?, ?, NULL, ?)
            ON CONFLICT(account_id)
            DO UPDATE SET
                regions_scanned = excluded.regions_scanned,
                errors_json = excluded.errors_json,
                last_error = NULL,
                synced_at = excluded.synced_at
            """,
            (
                account_id,
                max(0, int(regions_scanned)),
                json.dumps(errors, ensure_ascii=False),
                synced_at,
            ),
        )


def record_account_instance_sync_error(
    *,
    account_id: int,
    account_name: str,
    error: str,
) -> None:
    synced_at = _utc_now()
    error_item = {
        "account_id": account_id,
        "account_name": account_name,
        "region": None,
        "region_label": None,
        "error": error,
    }

    with database() as connection:
        connection.execute(
            """
            INSERT INTO oci_instance_cache_syncs (
                account_id,
                regions_scanned,
                errors_json,
                last_error,
                synced_at
            )
            VALUES (?, 0, ?, ?, ?)
            ON CONFLICT(account_id)
            DO UPDATE SET
                errors_json = excluded.errors_json,
                last_error = excluded.last_error,
                synced_at = excluded.synced_at
            """,
            (
                account_id,
                json.dumps([error_item], ensure_ascii=False),
                error,
                synced_at,
            ),
        )


def get_cached_instances(account_id: int | None = None) -> dict:
    where_clause = ""
    parameters: tuple[int, ...] = ()

    if account_id is not None:
        where_clause = "WHERE account_id = ?"
        parameters = (account_id,)

    with database() as connection:
        rows = connection.execute(
            f"""
            SELECT payload_json, synced_at
            FROM oci_instance_cache
            {where_clause}
            ORDER BY account_id, region, instance_id
            """,
            parameters,
        ).fetchall()
        sync_rows = connection.execute(
            f"""
            SELECT account_id, regions_scanned, errors_json, synced_at
            FROM oci_instance_cache_syncs
            {where_clause}
            ORDER BY account_id
            """,
            parameters,
        ).fetchall()

    instances: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        payload["cache_synced_at"] = row["synced_at"]
        instances.append(payload)

    errors: list[dict] = []
    for row in sync_rows:
        try:
            saved_errors = json.loads(row["errors_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            saved_errors = []
        if isinstance(saved_errors, list):
            errors.extend(item for item in saved_errors if isinstance(item, dict))

    synced_values = [row["synced_at"] for row in sync_rows if row["synced_at"]]
    return {
        "instances": instances,
        "errors": errors,
        "accounts_scanned": len(sync_rows),
        "regions_scanned": sum(int(row["regions_scanned"] or 0) for row in sync_rows),
        "cached": True,
        "last_synced_at": max(synced_values) if synced_values else None,
    }


def update_cached_instance(
    account_id: int,
    instance_id: str,
    changes: dict,
) -> bool:
    with database() as connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM oci_instance_cache
            WHERE account_id = ? AND instance_id = ?
            """,
            (account_id, instance_id),
        ).fetchone()

        if not row:
            return False

        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return False

        payload.update(
            {
                key: value
                for key, value in changes.items()
                if value is not None
            }
        )
        connection.execute(
            """
            UPDATE oci_instance_cache
            SET payload_json = ?
            WHERE account_id = ? AND instance_id = ?
            """,
            (
                json.dumps(payload, ensure_ascii=False),
                account_id,
                instance_id,
            ),
        )
        return True


def update_cached_public_ip(
    account_id: int,
    private_ip_id: str,
    new_ip: str | None,
) -> bool:
    with database() as connection:
        rows = connection.execute(
            """
            SELECT instance_id, payload_json
            FROM oci_instance_cache
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchall()

        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue

            changed = False
            for vnic in payload.get("vnics") or []:
                if vnic.get("private_ip_id") == private_ip_id:
                    vnic["public_ip"] = new_ip
                    changed = True

            if changed:
                connection.execute(
                    """
                    UPDATE oci_instance_cache
                    SET payload_json = ?
                    WHERE account_id = ? AND instance_id = ?
                    """,
                    (
                        json.dumps(payload, ensure_ascii=False),
                        account_id,
                        row["instance_id"],
                    ),
                )
                return True

    return False


def update_cached_boot_volume(
    account_id: int,
    volume_id: str,
    changes: dict,
) -> bool:
    with database() as connection:
        rows = connection.execute(
            """
            SELECT instance_id, payload_json
            FROM oci_instance_cache
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchall()

        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue

            changed = False
            for volume in payload.get("boot_volumes") or []:
                if volume.get("id") == volume_id:
                    volume.update(
                        {
                            key: value
                            for key, value in changes.items()
                            if value is not None
                        }
                    )
                    changed = True

            if changed:
                connection.execute(
                    """
                    UPDATE oci_instance_cache
                    SET payload_json = ?
                    WHERE account_id = ? AND instance_id = ?
                    """,
                    (
                        json.dumps(payload, ensure_ascii=False),
                        account_id,
                        row["instance_id"],
                    ),
                )
                return True

    return False


def get_cached_instance(account_id: int, instance_id: str) -> dict | None:
    with database() as connection:
        row = connection.execute(
            """
            SELECT payload_json, synced_at
            FROM oci_instance_cache
            WHERE account_id = ? AND instance_id = ?
            """,
            (account_id, instance_id),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    payload["cache_synced_at"] = row["synced_at"]
    return payload


def cached_private_ip_belongs_to_account(
    account_id: int,
    private_ip_id: str,
) -> bool:
    with database() as connection:
        rows = connection.execute(
            "SELECT payload_json FROM oci_instance_cache WHERE account_id = ?",
            (account_id,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for vnic in payload.get("vnics") or []:
            if str(vnic.get("private_ip_id") or "") == private_ip_id:
                return True
    return False


def cached_boot_volume_belongs_to_account(
    account_id: int,
    volume_id: str,
) -> bool:
    with database() as connection:
        rows = connection.execute(
            "SELECT payload_json FROM oci_instance_cache WHERE account_id = ?",
            (account_id,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for volume in payload.get("boot_volumes") or []:
            if str(volume.get("id") or "") == volume_id:
                return True
    return False
