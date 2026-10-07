import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .cloudflare_repository import (
    get_cloudflare_account,
    update_cloudflare_account_check,
    upsert_zones,
)
from .credential_crypto import decrypt_secret


class CloudflareError(RuntimeError):
    pass


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{40,80}$")
_INVISIBLE_CHARACTERS = str.maketrans("", "", "\u200b\u200c\u200d\u2060\ufeff")


def normalize_api_token(raw_token: str) -> str:
    token = str(raw_token or "").translate(_INVISIBLE_CHARACTERS).strip()

    if token.lower().startswith("authorization:"):
        token = token.split(":", 1)[1].strip()

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    token = token.strip("\"'").strip()

    if not token:
        raise CloudflareError("API Token 不能为空")

    if not _TOKEN_PATTERN.fullmatch(token):
        raise CloudflareError(
            "API Token 格式不正确。请只粘贴 Token 本身，不要包含 Bearer、"
            "Authorization、引号、空格或换行"
        )

    return token


def _cloudflare_error_message(body: dict, fallback: str) -> str:
    errors = body.get("errors") or []
    codes: list[str] = []
    messages: list[str] = []

    for error in errors:
        if not isinstance(error, dict):
            continue
        code = error.get("code")
        message = str(error.get("message") or "").strip()
        if code is not None:
            codes.append(str(code))
        if message:
            messages.append(message)

    combined = "; ".join(messages) or fallback
    lower = combined.lower()

    if "6003" in codes or "6111" in codes or "invalid request headers" in lower:
        return (
            "请求头无效。请只粘贴 Cloudflare API Token 本身；系统会自动处理 "
            "Bearer 前缀。不要粘贴 Global API Key、完整命令、引号或换行"
        )

    if (
        "9109" in codes
        or "10000" in codes
        or "authentication error" in lower
        or "invalid access token" in lower
    ):
        return "API Token 无效、已过期，或不属于当前 Cloudflare 账户"

    if "1001" in codes or "permission" in lower or "forbidden" in lower:
        return "API Token 权限不足。至少需要 Zone:Read 和 DNS:Edit 权限"

    return combined


def cf_request(
    token: str,
    method: str,
    path: str,
    payload: dict | None = None,
    query: dict | None = None,
) -> dict:
    normalized_token = normalize_api_token(token)
    url = "https://api.cloudflare.com/client/v4" + path

    if query:
        url += "?" + urllib.parse.urlencode(query)

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {normalized_token}",
        "Accept": "application/json",
        "User-Agent": "OCI-NT/1.0",
    }

    if data is not None:
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        raise CloudflareError(
            _cloudflare_error_message(body, f"Cloudflare HTTP {exc.code}")
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise CloudflareError(f"无法连接 Cloudflare：{reason}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CloudflareError("Cloudflare 返回了无法识别的数据") from exc

    if not body.get("success"):
        raise CloudflareError(_cloudflare_error_message(body, "Cloudflare 请求失败"))

    return body


def decrypt_cf_token(account_id: int) -> str:
    account = get_cloudflare_account(account_id)
    if not account:
        raise KeyError("Cloudflare 账户不存在")
    return normalize_api_token(decrypt_secret(account["api_token_encrypted"]))


def verify_token(token: str) -> dict:
    result = cf_request(token, "GET", "/user/tokens/verify")
    return result.get("result") or {}


def verify_zone_access(token: str) -> int:
    body = cf_request(token, "GET", "/zones", query={"page": 1, "per_page": 1})
    info = body.get("result_info") or {}
    return int(info.get("total_count") or len(body.get("result") or []))


def sync_zones(account_id: int) -> list[dict]:
    token = decrypt_cf_token(account_id)
    zones: list[dict] = []
    page = 1

    while True:
        body = cf_request(token, "GET", "/zones", query={"page": page, "per_page": 50})
        zones.extend(body.get("result") or [])
        info = body.get("result_info") or {}
        if page >= int(info.get("total_pages") or 1):
            break
        page += 1

    upsert_zones(account_id, zones)
    update_cloudflare_account_check(
        account_id,
        None,
        None,
        datetime.now(timezone.utc).isoformat(),
    )
    return zones


def list_dns_records(account_id: int, zone_id: str) -> list[dict]:
    token = decrypt_cf_token(account_id)
    records: list[dict] = []
    page = 1

    while True:
        body = cf_request(
            token,
            "GET",
            f"/zones/{zone_id}/dns_records",
            query={"page": page, "per_page": 100},
        )
        records.extend(body.get("result") or [])
        info = body.get("result_info") or {}
        if page >= int(info.get("total_pages") or 1):
            break
        page += 1

    return records


def create_dns_record(account_id: int, zone_id: str, payload: dict) -> dict:
    token = decrypt_cf_token(account_id)
    return cf_request(token, "POST", f"/zones/{zone_id}/dns_records", payload).get(
        "result"
    ) or {}


def update_dns_record(
    account_id: int,
    zone_id: str,
    record_id: str,
    payload: dict,
) -> dict:
    token = decrypt_cf_token(account_id)
    return cf_request(
        token,
        "PUT",
        f"/zones/{zone_id}/dns_records/{record_id}",
        payload,
    ).get("result") or {}


def delete_dns_record(account_id: int, zone_id: str, record_id: str) -> dict:
    token = decrypt_cf_token(account_id)
    return cf_request(
        token,
        "DELETE",
        f"/zones/{zone_id}/dns_records/{record_id}",
    ).get("result") or {}
