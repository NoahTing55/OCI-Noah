import ipaddress
import json
import time
from urllib.parse import urlsplit

import requests


SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}
SUPPORTED_ROTATE_METHODS = {"GET", "POST"}
BLOCKED_ROTATE_HEADERS = {
    "connection",
    "content-length",
    "host",
    "proxy-authorization",
    "transfer-encoding",
    "upgrade",
}


class ProxyConfigurationError(ValueError):
    pass


class ProxyRotationError(RuntimeError):
    pass


def normalize_proxy_url(raw_proxy_url: str) -> str:
    proxy_url = str(raw_proxy_url or "").strip()
    if not proxy_url:
        raise ProxyConfigurationError("代理地址不能为空")
    if "\n" in proxy_url or "\r" in proxy_url:
        raise ProxyConfigurationError("代理地址不能包含换行符")

    try:
        parsed = urlsplit(proxy_url)
        port = parsed.port
    except ValueError as exc:
        raise ProxyConfigurationError(f"代理端口格式错误：{exc}") from exc

    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        raise ProxyConfigurationError("仅支持 http、https、socks5、socks5h 代理")
    if not parsed.hostname:
        raise ProxyConfigurationError("代理地址缺少主机名或 IP")
    if port is None:
        raise ProxyConfigurationError("代理地址必须包含端口")
    if parsed.query or parsed.fragment:
        raise ProxyConfigurationError("代理地址不能包含查询参数或锚点")
    if parsed.path not in ("", "/"):
        raise ProxyConfigurationError("代理地址不能包含路径")
    return proxy_url


def mask_proxy_url(proxy_url: str) -> str:
    parsed = urlsplit(proxy_url)
    host = parsed.hostname or ""
    port = parsed.port or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authentication = "***:***@" if parsed.username else ""
    return f"{parsed.scheme}://{authentication}{host}:{port}"


def runtime_proxy_url(proxy_url: str) -> str:
    """Return the URL used for actual outbound requests.

    Requests/PySocks treats ``socks5://`` as local-DNS SOCKS and
    ``socks5h://`` as proxy-side DNS. OCI endpoints are hostname-based and
    many residential/datacenter SOCKS providers only route them reliably
    when the proxy resolves the hostname. Keep the stored/displayed scheme
    unchanged for compatibility, but execute SOCKS5 with remote DNS.
    """
    normalized = normalize_proxy_url(proxy_url)
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() == "socks5":
        return parsed._replace(scheme="socks5h").geturl()
    return normalized


def apply_proxy_to_oci_client(client: object, proxy_url: str | None) -> None:
    if proxy_url:
        effective = runtime_proxy_url(proxy_url)
        client.base_client.session.proxies = {"http": effective, "https": effective}


def _validated_ip(raw_value: object) -> str:
    ip_address = str(raw_value or "").strip()
    if not ip_address:
        raise ProxyConfigurationError("代理测试未返回出口 IP")
    try:
        ipaddress.ip_address(ip_address)
    except ValueError as exc:
        raise ProxyConfigurationError("代理测试返回了无效 IP") from exc
    return ip_address


def test_proxy_url_details(proxy_url: str) -> dict:
    normalized = normalize_proxy_url(proxy_url)
    effective = runtime_proxy_url(normalized)
    proxies = {"http": effective, "https": effective}
    headers = {"User-Agent": "OCI-NT/1.0.1", "Accept": "application/json"}
    geo_error: Exception | None = None

    started = time.monotonic()
    try:
        response = requests.get(
            "https://ipwho.is/",
            proxies=proxies,
            timeout=(10, 25),
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("success") is False:
            raise ProxyConfigurationError(str(payload.get("message") or "出口信息查询失败"))
        ip_address = _validated_ip(payload.get("ip"))
        latency_ms = max(1, int((time.monotonic() - started) * 1000))
        return {
            "ip_address": ip_address,
            "latency_ms": latency_ms,
            "country_code": str(payload.get("country_code") or "").strip().upper() or None,
            "country_name": str(payload.get("country") or "").strip() or None,
            "region_name": str(payload.get("region") or "").strip() or None,
            "city": str(payload.get("city") or "").strip() or None,
        }
    except Exception as exc:
        geo_error = exc

    started = time.monotonic()
    try:
        response = requests.get(
            "https://api64.ipify.org",
            params={"format": "json"},
            proxies=proxies,
            timeout=(10, 25),
            headers=headers,
        )
        response.raise_for_status()
        ip_address = _validated_ip(response.json().get("ip"))
        return {
            "ip_address": ip_address,
            "latency_ms": max(1, int((time.monotonic() - started) * 1000)),
            "country_code": None,
            "country_name": None,
            "region_name": None,
            "city": None,
        }
    except Exception as fallback_error:
        if geo_error is not None:
            raise ProxyConfigurationError(
                f"代理连接失败：{type(fallback_error).__name__}: {fallback_error}"
            ) from fallback_error
        raise


def test_proxy_url(proxy_url: str) -> str:
    return str(test_proxy_url_details(proxy_url)["ip_address"])


def normalize_rotate_api_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        raise ProxyConfigurationError("更换 IP API 地址不能为空")
    if len(value) > 4096:
        raise ProxyConfigurationError("更换 IP API 地址不能超过 4096 个字符")
    if "\n" in value or "\r" in value:
        raise ProxyConfigurationError("更换 IP API 地址不能包含换行符")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ProxyConfigurationError(f"更换 IP API 地址格式错误：{exc}") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ProxyConfigurationError("更换 IP API 仅支持 HTTP 或 HTTPS")
    if not parsed.hostname:
        raise ProxyConfigurationError("更换 IP API 地址缺少主机名")
    return value


def normalize_rotate_method(method: str | None) -> str:
    value = str(method or "GET").strip().upper()
    if value not in SUPPORTED_ROTATE_METHODS:
        raise ProxyConfigurationError("更换 IP API 仅支持 GET 或 POST")
    return value


def normalize_rotate_wait_seconds(value: int | str | None) -> int:
    try:
        seconds = int(value if value is not None else 3)
    except (TypeError, ValueError) as exc:
        raise ProxyConfigurationError("等待时间必须是整数") from exc
    if seconds < 0 or seconds > 60:
        raise ProxyConfigurationError("等待时间必须在 0–60 秒之间")
    return seconds


def normalize_rotate_headers(headers: dict[str, str] | str | None) -> dict[str, str]:
    if headers in (None, ""):
        return {}
    if isinstance(headers, str):
        try:
            loaded = json.loads(headers)
        except json.JSONDecodeError as exc:
            raise ProxyConfigurationError(f"请求头必须是 JSON 对象：{exc.msg}") from exc
    else:
        loaded = headers
    if not isinstance(loaded, dict):
        raise ProxyConfigurationError("请求头必须是 JSON 对象")
    if len(loaded) > 30:
        raise ProxyConfigurationError("请求头最多 30 项")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in loaded.items():
        key = str(raw_key or "").strip()
        if not key:
            raise ProxyConfigurationError("请求头名称不能为空")
        if key.lower() in BLOCKED_ROTATE_HEADERS:
            raise ProxyConfigurationError(f"不允许设置请求头：{key}")
        value = str(raw_value if raw_value is not None else "")
        if "\n" in key or "\r" in key or "\n" in value or "\r" in value:
            raise ProxyConfigurationError("请求头不能包含换行符")
        if len(key) > 120 or len(value) > 4096:
            raise ProxyConfigurationError("请求头名称或内容过长")
        normalized[key] = value
    return normalized


def normalize_rotate_body(body: str | None) -> str | None:
    if body is None:
        return None
    value = str(body)
    if len(value.encode("utf-8")) > 64 * 1024:
        raise ProxyConfigurationError("请求体不能超过 64 KiB")
    return value


def normalize_rotation_config(
    *,
    api_url: str,
    method: str | None = "GET",
    headers: dict[str, str] | str | None = None,
    body: str | None = None,
    wait_seconds: int | str | None = 3,
) -> dict:
    return {
        "api_url": normalize_rotate_api_url(api_url),
        "method": normalize_rotate_method(method),
        "headers": normalize_rotate_headers(headers),
        "body": normalize_rotate_body(body),
        "wait_seconds": normalize_rotate_wait_seconds(wait_seconds),
    }


def call_rotate_api(config: dict) -> int:
    api_url = normalize_rotate_api_url(config.get("api_url", ""))
    method = normalize_rotate_method(config.get("method"))
    headers = normalize_rotate_headers(config.get("headers"))
    body = normalize_rotate_body(config.get("body"))
    request_headers = {
        "User-Agent": "OCI-NT/1.0.1",
        "Accept": "application/json, text/plain, */*",
        **headers,
    }
    try:
        response = requests.request(
            method,
            api_url,
            headers=request_headers,
            data=body if method == "POST" else None,
            timeout=(10, 30),
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "未知"
        raise ProxyRotationError(f"更换 IP API 返回 HTTP {status_code}") from exc
    except requests.RequestException as exc:
        raise ProxyRotationError(f"更换 IP API 请求失败：{type(exc).__name__}") from exc
    return int(response.status_code)
