from .account_age import calculate_survival_days, earliest_registration_time
from .database import database


PUBLIC_COLUMNS = """
    a.id,
    a.custom_name,
    a.tenancy_ocid,
    a.user_ocid,
    a.fingerprint,
    a.region,
    a.email,
    a.user_name,
    a.user_lifecycle_state,
    a.user_time_created,
    a.tenancy_name,
    a.home_region_key,
    a.home_region_name,
    a.account_type,
    a.subscription_plan_type,
    a.subscription_upgrade_state,
    a.subscription_time_start,
    a.proxy_enabled,
    a.proxy_profile_id,
    CASE
        WHEN p.id IS NOT NULL OR a.proxy_url_encrypted IS NOT NULL THEN 1
        ELSE 0
    END AS has_proxy,
    p.name AS proxy_profile_name,
    COALESCE(p.name, a.proxy_label) AS proxy_label,
    COALESCE(p.last_ip, a.proxy_last_ip) AS proxy_last_ip,
    COALESCE(p.last_test_at, a.proxy_last_test_at) AS proxy_last_test_at,
    COALESCE(p.last_error, a.proxy_last_error) AS proxy_last_error,
    a.account_status,
    a.last_error,
    a.last_checked_at,
    a.created_at,
    a.updated_at
"""

PUBLIC_FROM = """
    oci_accounts a
    LEFT JOIN proxy_profiles p ON p.id = a.proxy_profile_id
"""



def row_dict(row):
    if not row:
        return None
    account = dict(row)
    registration_time = earliest_registration_time(
        [account.get("subscription_time_start"), account.get("user_time_created")]
    )
    account["registration_time"] = registration_time
    account["survival_days"] = calculate_survival_days(registration_time)
    return account


def create_account_record(**values) -> dict:
    columns = [
        "custom_name",
        "tenancy_ocid",
        "user_ocid",
        "fingerprint",
        "region",
        "private_key_encrypted",
        "passphrase_encrypted",
        "email",
        "user_name",
        "user_lifecycle_state",
        "user_time_created",
        "tenancy_name",
        "home_region_key",
        "home_region_name",
        "account_type",
        "subscription_plan_type",
        "subscription_upgrade_state",
        "subscription_time_start",
        "account_status",
        "last_error",
        "last_checked_at",
    ]
    placeholders = ", ".join("?" for _ in columns)
    with database() as connection:
        cursor = connection.execute(
            f"INSERT INTO oci_accounts ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values.get(column) for column in columns),
        )
        account_id = int(cursor.lastrowid)
    account = get_account_public(account_id)
    if not account:
        raise RuntimeError("账户保存后无法读取")
    return account



def find_account_by_credentials(
    tenancy_ocid: str,
    user_ocid: str,
    fingerprint: str,
) -> dict | None:
    with database() as connection:
        row = connection.execute(
            f"""
            SELECT {PUBLIC_COLUMNS}
            FROM {PUBLIC_FROM}
            WHERE a.tenancy_ocid = ? AND a.user_ocid = ? AND a.fingerprint = ?
            LIMIT 1
            """,
            (tenancy_ocid, user_ocid, fingerprint),
        ).fetchone()
    return row_dict(row)


def update_account_credentials(account_id: int, **values) -> dict | None:
    columns = [
        "tenancy_ocid",
        "user_ocid",
        "fingerprint",
        "region",
        "private_key_encrypted",
        "passphrase_encrypted",
        "email",
        "user_name",
        "user_lifecycle_state",
        "user_time_created",
        "tenancy_name",
        "home_region_key",
        "home_region_name",
        "account_type",
        "subscription_plan_type",
        "subscription_upgrade_state",
        "subscription_time_start",
        "account_status",
        "last_error",
        "last_checked_at",
    ]
    assignments = ", ".join(f"{column} = ?" for column in columns)
    with database() as connection:
        cursor = connection.execute(
            f"""
            UPDATE oci_accounts
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            tuple(values.get(column) for column in columns) + (int(account_id),),
        )
        if cursor.rowcount < 1:
            return None
    return get_account_public(int(account_id))

def list_accounts_public() -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM {PUBLIC_FROM} ORDER BY a.id DESC"
        ).fetchall()
    return [row_dict(row) for row in rows]


def get_account_public(account_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM {PUBLIC_FROM} WHERE a.id = ?",
            (account_id,),
        ).fetchone()
    return row_dict(row)


def get_account_with_secrets(account_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            """
            SELECT a.*,
                   p.proxy_url_encrypted AS profile_proxy_url_encrypted,
                   p.name AS profile_proxy_name,
                   p.last_ip AS profile_proxy_last_ip,
                   p.last_test_at AS profile_proxy_last_test_at,
                   p.last_error AS profile_proxy_last_error
            FROM oci_accounts a
            LEFT JOIN proxy_profiles p ON p.id = a.proxy_profile_id
            WHERE a.id = ?
            """,
            (account_id,),
        ).fetchone()
    return row_dict(row)


def update_account_check(account_id: int, **values) -> dict | None:
    columns = [
        "email",
        "user_name",
        "user_lifecycle_state",
        "user_time_created",
        "tenancy_name",
        "home_region_key",
        "home_region_name",
        "account_type",
        "subscription_plan_type",
        "subscription_upgrade_state",
        "subscription_time_start",
        "account_status",
        "last_error",
        "last_checked_at",
    ]
    assignments = ", ".join(f"{column} = ?" for column in columns)
    with database() as connection:
        connection.execute(
            f"UPDATE oci_accounts SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(values.get(column) for column in columns) + (account_id,),
        )
    return get_account_public(account_id)



def update_account_metadata(account_id: int, *, custom_name: str) -> dict | None:
    with database() as connection:
        connection.execute(
            """
            UPDATE oci_accounts
            SET custom_name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (custom_name, int(account_id)),
        )
    return get_account_public(account_id)

def delete_account_record(account_id: int) -> bool:
    with database() as connection:
        cursor = connection.execute("DELETE FROM oci_accounts WHERE id = ?", (account_id,))
        return cursor.rowcount > 0


def generate_unique_custom_name(base_name: str) -> str:
    base_name = base_name.strip() or "OCI账户"
    with database() as connection:
        rows = connection.execute("SELECT custom_name FROM oci_accounts").fetchall()
    existing_names = {str(row["custom_name"]) for row in rows if row["custom_name"]}
    if base_name not in existing_names:
        return base_name
    sequence = 2
    while f"{base_name}-{sequence}" in existing_names:
        sequence += 1
    return f"{base_name}-{sequence}"


def get_account_proxy_settings(account_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            """
            SELECT a.id, a.proxy_enabled, a.proxy_profile_id,
                   COALESCE(p.proxy_url_encrypted, a.proxy_url_encrypted) AS proxy_url_encrypted,
                   p.name AS proxy_profile_name,
                   COALESCE(p.name, a.proxy_label) AS proxy_label,
                   COALESCE(p.last_ip, a.proxy_last_ip) AS proxy_last_ip,
                   COALESCE(p.last_test_at, a.proxy_last_test_at) AS proxy_last_test_at,
                   COALESCE(p.last_error, a.proxy_last_error) AS proxy_last_error
            FROM oci_accounts a
            LEFT JOIN proxy_profiles p ON p.id = a.proxy_profile_id
            WHERE a.id = ?
            """,
            (account_id,),
        ).fetchone()
    return row_dict(row)


def save_account_proxy(
    *,
    account_id: int,
    enabled: bool,
    proxy_label: str | None,
    proxy_url_encrypted: str | None = None,
    clear_proxy: bool = False,
) -> dict | None:
    with database() as connection:
        if clear_proxy:
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
                WHERE id = ?
                """,
                (account_id,),
            )
        elif proxy_url_encrypted is not None:
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_enabled = ?,
                    proxy_url_encrypted = ?,
                    proxy_label = ?,
                    proxy_last_ip = NULL,
                    proxy_last_test_at = NULL,
                    proxy_last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (1 if enabled else 0, proxy_url_encrypted, proxy_label, account_id),
            )
        else:
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_enabled = ?, proxy_label = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (1 if enabled else 0, proxy_label, account_id),
            )
    return get_account_proxy_settings(account_id)


def save_proxy_test_result(account_id: int, ip_address: str | None, tested_at: str, error: str | None) -> None:
    with database() as connection:
        row = connection.execute(
            "SELECT proxy_profile_id FROM oci_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if row and row["proxy_profile_id"] is not None:
            profile_id = int(row["proxy_profile_id"])
            connection.execute(
                """
                UPDATE proxy_profiles
                SET last_ip = ?, last_test_at = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (ip_address, tested_at, error, profile_id),
            )
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_last_ip = ?, proxy_last_test_at = ?, proxy_last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE proxy_profile_id = ?
                """,
                (ip_address, tested_at, error, profile_id),
            )
        else:
            connection.execute(
                """
                UPDATE oci_accounts
                SET proxy_last_ip = ?, proxy_last_test_at = ?, proxy_last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (ip_address, tested_at, error, account_id),
            )

