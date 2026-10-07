import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import settings
from .security import hash_password

database_maintenance_lock = threading.RLock()


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    with database_maintenance_lock:
        connection = connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


def init_database() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)

    from .schema_migrations import recover_interrupted_runtime_state, run_schema_migrations

    connection = connect()
    try:
        run_schema_migrations(connection)
        recover_interrupted_runtime_state(connection)
    finally:
        connection.close()

def ensure_admin_user(username: str, password: str) -> None:
    with database() as connection:
        existing = connection.execute(
            "SELECT id FROM users ORDER BY id LIMIT 1"
        ).fetchone()
        if existing:
            return
        connection.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, hash_password(password)),
        )


def get_user_by_username(username: str) -> dict | None:
    with database() as connection:
        row = connection.execute(
            """
            SELECT id, username, password_hash, role, is_active,
                   last_login_at, token_version, failed_login_count, locked_until,
                   created_at, updated_at
            FROM users WHERE username = ?
            """,
            (username,),
        ).fetchone()
    return dict(row) if row else None


def get_first_active_user() -> dict | None:
    with database() as connection:
        row = connection.execute(
            """
            SELECT id, username, password_hash, role, is_active,
                   last_login_at, token_version, failed_login_count, locked_until,
                   created_at, updated_at
            FROM users
            WHERE is_active = 1
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def update_user_password(username: str, password: str) -> bool:
    if password == "":
        raise ValueError("密码不能为空")
    with database() as connection:
        cursor = connection.execute(
            """
            UPDATE users
            SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
            WHERE username = ?
            """,
            (hash_password(password), username),
        )
        return cursor.rowcount > 0


def update_user_credentials(
    current_username: str,
    *,
    new_username: str | None = None,
    new_password: str | None = None,
) -> dict:
    normalized_username = (
        new_username.strip() if new_username is not None else current_username
    )
    if not normalized_username:
        raise ValueError("用户名不能为空")
    if new_password is not None and new_password == "":
        raise ValueError("密码不能为空")

    with database() as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            (current_username,),
        ).fetchone()
        if not user:
            raise KeyError("用户不存在")

        duplicate = connection.execute(
            "SELECT id FROM users WHERE username = ? AND id <> ?",
            (normalized_username, user["id"]),
        ).fetchone()
        if duplicate:
            raise ValueError("该用户名已存在")

        password_hash = (
            hash_password(new_password)
            if new_password is not None
            else user["password_hash"]
        )
        connection.execute(
            """
            UPDATE users
            SET username = ?, password_hash = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (normalized_username, password_hash, user["id"]),
        )
        updated = connection.execute(
            """
            SELECT id, username, password_hash, role, is_active,
                   last_login_at, token_version, failed_login_count, locked_until,
                   created_at, updated_at
            FROM users WHERE id = ?
            """,
            (user["id"],),
        ).fetchone()
    return dict(updated)


def create_oauth_login_code(
    *,
    code_hash: str,
    username: str,
    expires_at: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with database() as connection:
        connection.execute(
            "DELETE FROM oauth_login_codes WHERE expires_at < ? OR used_at IS NOT NULL",
            (now,),
        )
        connection.execute(
            """
            INSERT INTO oauth_login_codes
                (code_hash, username, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (code_hash, username, expires_at, now),
        )


def consume_oauth_login_code(code_hash: str) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    with database() as connection:
        row = connection.execute(
            """
            SELECT code_hash, username, expires_at, used_at
            FROM oauth_login_codes
            WHERE code_hash = ?
            """,
            (code_hash,),
        ).fetchone()
        if not row or row["used_at"] is not None or row["expires_at"] < now:
            return None
        connection.execute(
            "UPDATE oauth_login_codes SET used_at = ? WHERE code_hash = ?",
            (now, code_hash),
        )
    return dict(row)


def add_audit_log(
    username: str | None,
    action: str,
    ip_address: str | None,
    detail: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> None:
    with database() as connection:
        connection.execute(
            """
            INSERT INTO audit_logs
                (username, action, resource_type, resource_id, detail, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (username, action, resource_type, resource_id, detail, ip_address),
        )


def list_audit_logs(
    limit: int = 100,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> list[dict]:
    """Return recent audit rows with optional resource scoping.

    The filters are intentionally exact-match and parameterized so the instance
    workspace can render its operation history without loading unrelated logs.
    """
    limit = max(1, min(limit, 500))
    clauses: list[str] = []
    params: list[object] = []
    if resource_type:
        clauses.append("resource_type = ?")
        params.append(str(resource_type))
    if resource_id:
        clauses.append("resource_id = ?")
        params.append(str(resource_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with database() as connection:
        rows = connection.execute(
            f"""
            SELECT id, username, action, resource_type, resource_id,
                   detail, ip_address, created_at
            FROM audit_logs
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def create_oauth_login_state(*, state_hash: str, nonce: str, expires_at: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with database() as connection:
        connection.execute(
            "DELETE FROM oauth_login_states WHERE expires_at < ? OR used_at IS NOT NULL",
            (now,),
        )
        connection.execute(
            """
            INSERT INTO oauth_login_states
                (state_hash, nonce, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (state_hash, nonce, expires_at, now),
        )


def consume_oauth_login_state(state_hash: str) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    with database() as connection:
        row = connection.execute(
            """
            SELECT state_hash, nonce, expires_at, used_at
            FROM oauth_login_states
            WHERE state_hash = ?
            """,
            (state_hash,),
        ).fetchone()
        if not row or row["used_at"] is not None or row["expires_at"] < now:
            return None
        connection.execute(
            "UPDATE oauth_login_states SET used_at = ? WHERE state_hash = ?",
            (now, state_hash),
        )
    return dict(row)
