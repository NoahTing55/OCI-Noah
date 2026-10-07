from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from .config import settings
from .database import database, get_user_by_username
from .security import create_access_token, decode_access_token_payload


LOCK_THRESHOLD = 5
LOCK_WINDOW_MINUTES = 15
LOCK_DURATION_MINUTES = 15
IP_FAILURE_THRESHOLD = 20
AUTH_HISTORY_DAYS = 30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_agent(value: str | None) -> str:
    return str(value or "").strip()[:500]


def _ip(value: str | None) -> str | None:
    normalized = str(value or "").strip()[:100]
    return normalized or None


def login_ip_rate_status(ip_address: str | None) -> dict:
    normalized_ip = _ip(ip_address)
    if not normalized_ip or normalized_ip == "LOCAL":
        return {"limited": False, "failed_count": 0}
    window_start = (
        datetime.now(timezone.utc) - timedelta(minutes=LOCK_WINDOW_MINUTES)
    ).isoformat()
    with database() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) FROM login_attempts
            WHERE ip_address = ? AND success = 0 AND created_at >= ?
            """,
            (normalized_ip, window_start),
        ).fetchone()
    failed_count = int(row[0] if row else 0)
    return {
        "limited": failed_count >= IP_FAILURE_THRESHOLD,
        "failed_count": failed_count,
    }


def cleanup_auth_history() -> dict:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=AUTH_HISTORY_DAYS)
    ).isoformat()
    with database() as connection:
        attempts = connection.execute(
            "DELETE FROM login_attempts WHERE created_at < ?", (cutoff,)
        ).rowcount
        sessions = connection.execute(
            """
            DELETE FROM auth_sessions
            WHERE expires_at < ?
               OR (revoked_at IS NOT NULL AND revoked_at < ?)
            """,
            (cutoff, cutoff),
        ).rowcount
    return {"login_attempts": attempts, "auth_sessions": sessions}


def user_lock_status(username: str) -> dict:
    user = get_user_by_username(username)
    if not user:
        return {"locked": False, "locked_until": None, "failed_login_count": 0}
    locked_until = user.get("locked_until")
    locked = False
    if locked_until:
        try:
            locked = datetime.fromisoformat(str(locked_until)) > datetime.now(timezone.utc)
        except ValueError:
            locked = False
    return {
        "locked": locked,
        "locked_until": locked_until if locked else None,
        "failed_login_count": int(user.get("failed_login_count") or 0),
    }


def record_login_failure(username: str, ip_address: str | None, user_agent: str | None, reason: str) -> dict:
    now = datetime.now(timezone.utc)
    normalized_username = str(username or "").strip()[:120]
    with database() as connection:
        connection.execute(
            """
            INSERT INTO login_attempts(username, ip_address, user_agent, success, reason, created_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (normalized_username or None, _ip(ip_address), _user_agent(user_agent), str(reason)[:500], now.isoformat()),
        )
        user = connection.execute(
            "SELECT id, failed_login_count, locked_until FROM users WHERE username = ?",
            (normalized_username,),
        ).fetchone()
        if not user:
            return {"locked": False, "locked_until": None, "failed_login_count": 0}
        window_start = (now - timedelta(minutes=LOCK_WINDOW_MINUTES)).isoformat()
        recent = connection.execute(
            """
            SELECT COUNT(*) FROM login_attempts
            WHERE username = ? AND success = 0 AND created_at >= ?
            """,
            (normalized_username, window_start),
        ).fetchone()
        current = int(recent[0] if recent else 1)
        normalized_ip = _ip(ip_address)
        recent_ip_count = 0
        if normalized_ip and normalized_ip != "LOCAL":
            recent_ip = connection.execute(
                """
                SELECT COUNT(*) FROM login_attempts
                WHERE username = ? AND ip_address = ?
                  AND success = 0 AND created_at >= ?
                """,
                (normalized_username, normalized_ip, window_start),
            ).fetchone()
            recent_ip_count = int(recent_ip[0] if recent_ip else 0)
        locked_until = None
        # 同一来源连续失败才锁账户，避免第三方仅凭用户名恶意锁死管理员。
        if current >= LOCK_THRESHOLD and recent_ip_count >= LOCK_THRESHOLD:
            locked_until = (now + timedelta(minutes=LOCK_DURATION_MINUTES)).isoformat()
        connection.execute(
            """
            UPDATE users
            SET failed_login_count = ?, locked_until = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (current, locked_until, int(user["id"])),
        )
    return {"locked": bool(locked_until), "locked_until": locked_until, "failed_login_count": current}


def record_login_success(username: str, ip_address: str | None, user_agent: str | None, method: str) -> None:
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO login_attempts(username, ip_address, user_agent, success, reason, created_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (str(username)[:120], _ip(ip_address), _user_agent(user_agent), str(method)[:40], now),
        )
        connection.execute(
            """
            UPDATE users
            SET failed_login_count = 0, locked_until = NULL, last_login_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE username = ?
            """,
            (now, username),
        )


def create_login_session(
    *,
    username: str,
    ip_address: str | None,
    user_agent: str | None,
    login_method: str,
) -> tuple[str, dict]:
    user = get_user_by_username(username)
    if not user or not user.get("is_active"):
        raise KeyError("用户不存在或已停用")
    session_id = secrets.token_urlsafe(24)
    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(minutes=settings.access_token_minutes)
    token_version = int(user.get("token_version") or 0)
    token = create_access_token(
        username,
        session_id=session_id,
        token_version=token_version,
    )
    with database() as connection:
        connection.execute(
            """
            INSERT INTO auth_sessions(
                session_id, user_id, username, token_version, login_method,
                ip_address, user_agent, created_at, last_seen_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                int(user["id"]),
                username,
                token_version,
                str(login_method or "LOCAL").upper()[:30],
                _ip(ip_address),
                _user_agent(user_agent),
                issued.isoformat(),
                issued.isoformat(),
                expires.isoformat(),
            ),
        )
    record_login_success(username, ip_address, user_agent, login_method)
    return token, get_session(session_id) or {}


def get_session(session_id: str) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM auth_sessions WHERE session_id = ?",
            (str(session_id),),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["is_current"] = False
    result["active"] = not result.get("revoked_at") and str(result.get("expires_at") or "") > utc_now()
    return result


def validate_access_token(token: str, *, touch: bool = True) -> tuple[dict, dict | None] | None:
    payload = decode_access_token_payload(token)
    if not payload:
        return None
    username = payload.get("sub")
    if not isinstance(username, str) or not username:
        return None
    user = get_user_by_username(username)
    if not user or not user.get("is_active"):
        return None
    token_version = int(payload.get("ver") or 0)
    if token_version != int(user.get("token_version") or 0):
        return None
    session_id = payload.get("sid")
    if not session_id:
        # V1.0.3 tokens are accepted until expiry unless credentials changed.
        return user, None
    session = get_session(str(session_id))
    if not session or not session.get("active"):
        return None
    if int(session.get("user_id") or 0) != int(user["id"]):
        return None
    if int(session.get("token_version") or 0) != token_version:
        return None
    if touch:
        with database() as connection:
            connection.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE session_id = ?",
                (utc_now(), str(session_id)),
            )
    session["is_current"] = True
    return user, session


def list_user_sessions(user_id: int, *, current_session_id: str | None = None) -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            """
            SELECT * FROM auth_sessions
            WHERE user_id = ?
            ORDER BY COALESCE(last_seen_at, created_at) DESC
            LIMIT 100
            """,
            (int(user_id),),
        ).fetchall()
    now = utc_now()
    sessions = []
    for row in rows:
        item = dict(row)
        item["active"] = not item.get("revoked_at") and str(item.get("expires_at") or "") > now
        item["is_current"] = bool(current_session_id and item["session_id"] == current_session_id)
        sessions.append(item)
    return sessions


def revoke_session(session_id: str, user_id: int, *, reason: str = "USER_REVOKED") -> bool:
    with database() as connection:
        cursor = connection.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = ?
            WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL
            """,
            (utc_now(), str(reason)[:100], str(session_id), int(user_id)),
        )
    return cursor.rowcount > 0


def revoke_other_sessions(user_id: int, current_session_id: str | None) -> int:
    with database() as connection:
        if current_session_id:
            cursor = connection.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = 'LOGOUT_OTHERS'
                WHERE user_id = ? AND session_id <> ? AND revoked_at IS NULL
                """,
                (utc_now(), int(user_id), str(current_session_id)),
            )
        else:
            cursor = connection.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = 'LOGOUT_OTHERS'
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (utc_now(), int(user_id)),
            )
    return int(cursor.rowcount)


def invalidate_user_sessions(user_id: int, *, reason: str, increment_token_version: bool = True) -> int:
    now = utc_now()
    with database() as connection:
        cursor = connection.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = ?
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (now, str(reason)[:100], int(user_id)),
        )
        if increment_token_version:
            connection.execute(
                "UPDATE users SET token_version = token_version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(user_id),),
            )
    return int(cursor.rowcount)


def purge_expired_sessions(retention_days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))).isoformat()
    with database() as connection:
        cursor = connection.execute(
            """
            DELETE FROM auth_sessions
            WHERE (expires_at < ? OR revoked_at IS NOT NULL)
              AND COALESCE(revoked_at, expires_at) < ?
            """,
            (utc_now(), cutoff),
        )
    return int(cursor.rowcount)


def recent_login_attempts(limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    with database() as connection:
        rows = connection.execute(
            "SELECT * FROM login_attempts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def token_session_id(token: str) -> str | None:
    payload = decode_access_token_payload(token)
    sid = payload.get("sid") if payload else None
    return str(sid) if sid else None
