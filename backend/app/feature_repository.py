from datetime import datetime, timezone

from .database import database


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()




























def touch_user_login(username: str) -> None:
    with database() as connection:
        connection.execute(
            "UPDATE users SET last_login_at = ? WHERE username = ?",
            (_utc_now(), username),
        )






