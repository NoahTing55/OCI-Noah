import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import requests

from .database import (
    consume_oauth_login_code,
    consume_oauth_login_state,
    create_oauth_login_code,
    create_oauth_login_state,
    get_first_active_user,
)
from .settings_repository import load_google_auth_settings

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_DISCOVERY_ENDPOINT = "https://accounts.google.com/.well-known/openid-configuration"


class GoogleOAuthError(RuntimeError):
    pass


def _configuration(*, include_secret: bool = False) -> dict:
    config = load_google_auth_settings(include_secret=include_secret)
    if not config["enabled"]:
        raise GoogleOAuthError("Gmail 登录已停用")
    if not config["configured"]:
        raise GoogleOAuthError("Gmail 登录配置不完整")
    return config


def google_oauth_status() -> dict:
    config = load_google_auth_settings(include_secret=False)
    reason = None
    if not config["enabled"]:
        reason = "Gmail 登录已停用"
    elif not config["configured"]:
        reason = "Gmail 登录配置不完整"
    return {
        "enabled": bool(config["enabled"] and config["configured"]),
        "configured": bool(config["configured"]),
        "reason": reason,
    }


def build_authorization_url() -> str:
    config = _configuration(include_secret=True)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    create_oauth_login_state(state_hash=state_hash, nonce=nonce, expires_at=expires_at)
    query = urlencode({
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "access_type": "online",
        "prompt": "select_account",
    })
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}"


def _consume_state(state: str) -> str:
    if not state:
        raise GoogleOAuthError("Google 登录状态缺失，请重新登录")
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    record = consume_oauth_login_state(state_hash)
    if not record:
        raise GoogleOAuthError("Google 登录状态无效、已使用或已过期")
    return str(record["nonce"])


def exchange_google_code(code: str, state: str) -> str:
    config = _configuration(include_secret=True)
    expected_nonce = _consume_state(state)
    try:
        response = requests.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uri": config["redirect_uri"],
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise GoogleOAuthError(f"Google Token 请求失败：{exc}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("error_description") or response.json().get("error")
        except Exception:
            detail = None
        raise GoogleOAuthError(f"Google Token 交换失败{f'：{detail}' if detail else ''}")
    token_payload = response.json()
    raw_id_token = token_payload.get("id_token")
    if not raw_id_token:
        raise GoogleOAuthError("Google 未返回身份令牌")
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token as google_id_token
        claims = google_id_token.verify_oauth2_token(
            raw_id_token,
            GoogleRequest(),
            config["client_id"],
        )
    except Exception as exc:
        raise GoogleOAuthError("Google 身份令牌验证失败") from exc

    if str(claims.get("nonce") or "") != expected_nonce:
        raise GoogleOAuthError("Google 登录 nonce 校验失败，请重新登录")
    email = str(claims.get("email") or "").strip().lower()
    email_verified = bool(claims.get("email_verified"))
    allowed_emails = {item.lower() for item in config["allowed_emails"]}
    if not email_verified:
        raise GoogleOAuthError("Google 未确认该邮箱的所有权")
    if email not in allowed_emails:
        raise GoogleOAuthError("该 Google 账号不在允许登录名单中")

    user = get_first_active_user()
    if not user:
        raise GoogleOAuthError("本地管理员账户不存在")
    one_time_code = secrets.token_urlsafe(32)
    code_hash = hashlib.sha256(one_time_code.encode("utf-8")).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    create_oauth_login_code(code_hash=code_hash, username=user["username"], expires_at=expires_at)
    return one_time_code


def consume_frontend_code(code: str) -> str | None:
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    record = consume_oauth_login_code(code_hash)
    return record["username"] if record else None


def frontend_return_url(*, code: str | None = None, error: str | None = None) -> str:
    config = load_google_auth_settings(include_secret=False)
    target = config.get("frontend_return_url") or "/"
    parsed = urlparse(target)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if code:
        query["google_code"] = code
    if error:
        query["google_error"] = error
    return urlunparse(parsed._replace(query=urlencode(query)))


def test_google_configuration() -> dict:
    config = load_google_auth_settings(include_secret=True)
    missing = []
    if not config.get("client_id"):
        missing.append("Client ID")
    if not config.get("client_secret"):
        missing.append("Client Secret")
    if not config.get("redirect_uri"):
        missing.append("回调地址")
    if not config.get("allowed_emails"):
        missing.append("允许邮箱")
    if missing:
        raise GoogleOAuthError("配置不完整：" + "、".join(missing))
    try:
        response = requests.get(GOOGLE_DISCOVERY_ENDPOINT, timeout=15)
        response.raise_for_status()
        discovery = response.json()
    except requests.RequestException as exc:
        raise GoogleOAuthError(f"无法访问 Google OpenID 配置：{exc}") from exc
    if discovery.get("authorization_endpoint") != GOOGLE_AUTHORIZATION_ENDPOINT:
        raise GoogleOAuthError("Google OpenID 配置返回异常")
    return {
        "ok": True,
        "message": "配置格式正确，服务器可访问 Google OpenID 服务",
        "redirect_uri": config["redirect_uri"],
        "allowed_count": len(config["allowed_emails"]),
    }
