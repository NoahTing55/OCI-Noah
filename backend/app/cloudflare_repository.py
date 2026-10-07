from .database import database


def list_cloudflare_accounts() -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            """
            SELECT id, name, email, last_error, last_checked_at, created_at, updated_at,
                   CASE WHEN api_token_encrypted IS NULL THEN 0 ELSE 1 END AS has_token
            FROM cloudflare_accounts
            ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_cloudflare_account(account_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute("SELECT * FROM cloudflare_accounts WHERE id = ?", (account_id,)).fetchone()
    return dict(row) if row else None


def create_cloudflare_account(name: str, email: str | None, api_token_encrypted: str) -> dict:
    with database() as connection:
        cursor = connection.execute(
            "INSERT INTO cloudflare_accounts (name, email, api_token_encrypted) VALUES (?, ?, ?)",
            (name, email, api_token_encrypted),
        )
        account_id = int(cursor.lastrowid)
    account = get_cloudflare_account(account_id)
    if not account:
        raise RuntimeError("Cloudflare 账户保存后无法读取")
    account.pop("api_token_encrypted", None)
    account["has_token"] = True
    return account


def update_cloudflare_account_check(account_id: int, email: str | None, error: str | None, checked_at: str) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE cloudflare_accounts
            SET email = COALESCE(?, email), last_error = ?, last_checked_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (email, error, checked_at, account_id),
        )


def delete_cloudflare_account(account_id: int) -> bool:
    with database() as connection:
        cursor = connection.execute("DELETE FROM cloudflare_accounts WHERE id = ?", (account_id,))
        return cursor.rowcount > 0


def upsert_zones(account_id: int, zones: list[dict]) -> None:
    with database() as connection:
        for zone in zones:
            connection.execute(
                """
                INSERT INTO cloudflare_zones (account_id, zone_id, name, status, paused, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(account_id, zone_id)
                DO UPDATE SET name = excluded.name,
                              status = excluded.status,
                              paused = excluded.paused,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (account_id, zone["id"], zone["name"], zone.get("status"), 1 if zone.get("paused") else 0),
            )


def list_zones(account_id: int) -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            """
            SELECT id, account_id, zone_id, name, status, paused, created_at, updated_at
            FROM cloudflare_zones
            WHERE account_id = ?
            ORDER BY name ASC
            """,
            (account_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_zone(account_id: int, zone_id: str) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM cloudflare_zones WHERE account_id = ? AND zone_id = ?",
            (account_id, zone_id),
        ).fetchone()
    return dict(row) if row else None
