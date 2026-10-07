import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from jwt.exceptions import InvalidTokenError

from .config import settings

try:
    from pwdlib import PasswordHash
except ImportError:  # pragma: no cover - local static-test fallback
    PasswordHash = None

ALGORITHM = "HS256"
_PBKDF2_ITERATIONS = 600_000
password_hasher = PasswordHash.recommended() if PasswordHash else None


def _fallback_hash(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def _fallback_verify(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, encoded_salt, encoded_digest = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def hash_password(password: str) -> str:
    if password_hasher:
        return password_hasher.hash(password)
    return _fallback_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        if password_hasher:
            return password_hasher.verify(password, password_hash)
        return _fallback_verify(password, password_hash)
    except Exception:
        return False


def create_access_token(
    username: str,
    *,
    session_id: str | None = None,
    token_version: int = 0,
) -> str:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": username,
        "iat": issued_at,
        "exp": expires_at,
        "jti": secrets.token_urlsafe(18),
        "ver": int(token_version),
    }
    if session_id:
        payload["sid"] = str(session_id)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token_payload(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return payload if isinstance(payload, dict) else None
    except InvalidTokenError:
        return None


def decode_access_token(token: str) -> str | None:
    payload = decode_access_token_payload(token)
    username = payload.get("sub") if payload else None
    return username if isinstance(username, str) and username else None
