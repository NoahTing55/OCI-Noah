import csv
import json
import re
from dataclasses import dataclass
from io import StringIO
from urllib.parse import quote, urlsplit

from .proxy_service import (
    SUPPORTED_PROXY_SCHEMES,
    normalize_proxy_url,
    normalize_rotation_config,
)


MAX_IMPORT_ITEMS = 500
MAX_IMPORT_BYTES = 512 * 1024


@dataclass
class ProxyImportItem:
    line_number: int
    name: str
    proxy_url: str
    rotate_api_url: str | None = None
    rotate_api_method: str = "GET"
    rotate_api_headers: dict[str, str] | None = None
    rotate_api_body: str | None = None
    rotate_wait_seconds: int = 3


class ProxyImportError(ValueError):
    pass


def _first(mapping: dict, *names: str):
    lowered = {str(key).strip().casefold(): value for key, value in mapping.items()}
    for name in names:
        if name.casefold() in lowered:
            return lowered[name.casefold()]
    return None


def _clean_name(value: object, fallback: str) -> str:
    name = str(value or "").strip() or fallback
    if len(name) > 120:
        raise ProxyImportError("代理名称不能超过 120 个字符")
    return name


def _host_with_brackets(host: str) -> str:
    clean = str(host or "").strip().strip("[]")
    if not clean:
        raise ProxyImportError("代理地址缺少服务器")
    return f"[{clean}]" if ":" in clean else clean


def _build_proxy_url(
    *,
    scheme: object,
    host: object,
    port: object,
    username: object = None,
    password: object = None,
) -> str:
    protocol = str(scheme or "socks5").strip().lower()
    if protocol not in SUPPORTED_PROXY_SCHEMES:
        raise ProxyImportError("代理协议仅支持 http、https、socks5、socks5h")
    try:
        port_number = int(str(port).strip())
    except (TypeError, ValueError) as exc:
        raise ProxyImportError("代理端口必须是整数") from exc
    if port_number < 1 or port_number > 65535:
        raise ProxyImportError("代理端口必须在 1–65535 之间")
    user = str(username or "")
    secret = str(password or "")
    if secret and not user:
        raise ProxyImportError("填写密码时必须同时填写用户名")
    auth = ""
    if user:
        auth = quote(user, safe="")
        if secret:
            auth += f":{quote(secret, safe='')}"
        auth += "@"
    return normalize_proxy_url(
        f"{protocol}://{auth}{_host_with_brackets(str(host))}:{port_number}"
    )


def _rotation_from_mapping(mapping: dict) -> dict:
    url = _first(
        mapping,
        "rotate_api_url",
        "change_ip_api",
        "change_ip_url",
        "rotate_url",
        "api_url",
        "换ipapi",
        "换_ip_api",
    )
    if not str(url or "").strip():
        return {
            "rotate_api_url": None,
            "rotate_api_method": "GET",
            "rotate_api_headers": None,
            "rotate_api_body": None,
            "rotate_wait_seconds": 3,
        }
    headers = _first(mapping, "rotate_api_headers", "headers", "request_headers", "请求头")
    body = _first(mapping, "rotate_api_body", "body", "request_body", "请求体")
    config = normalize_rotation_config(
        api_url=str(url),
        method=str(_first(mapping, "rotate_api_method", "method", "请求方法") or "GET"),
        headers=headers,
        body=None if body is None else str(body),
        wait_seconds=_first(mapping, "rotate_wait_seconds", "wait_seconds", "wait", "等待秒数") or 3,
    )
    return {
        "rotate_api_url": config["api_url"],
        "rotate_api_method": config["method"],
        "rotate_api_headers": config["headers"],
        "rotate_api_body": config["body"],
        "rotate_wait_seconds": config["wait_seconds"],
    }


def _item_from_mapping(mapping: dict, index: int, prefix: str, line_number: int) -> ProxyImportItem:
    fallback_name = f"{prefix}-{index}"
    name = _clean_name(_first(mapping, "name", "label", "remark", "备注", "名称"), fallback_name)
    direct_url = _first(mapping, "proxy_url", "url", "proxy", "代理", "代理地址")
    if str(direct_url or "").strip():
        proxy_url = normalize_proxy_url(str(direct_url).strip())
    else:
        proxy_url = _build_proxy_url(
            scheme=_first(mapping, "scheme", "protocol", "type", "协议") or "socks5",
            host=_first(mapping, "host", "server", "ip", "服务器", "地址"),
            port=_first(mapping, "port", "端口"),
            username=_first(mapping, "username", "user", "account", "用户名"),
            password=_first(mapping, "password", "pass", "pwd", "密码"),
        )
    rotation = _rotation_from_mapping(mapping)
    return ProxyImportItem(
        line_number=line_number,
        name=name,
        proxy_url=proxy_url,
        **rotation,
    )


def _split_delimited(line: str) -> list[str] | None:
    delimiter = None
    for candidate in ("\t", "|", ",", ";"):
        if candidate in line:
            delimiter = candidate
            break
    if delimiter is None:
        return None
    reader = csv.reader(StringIO(line), delimiter=delimiter, skipinitialspace=True)
    return [part.strip() for part in next(reader)]


def _parse_colon_format(line: str, default_scheme: str) -> dict | None:
    # user:pass@host:port
    auth_match = re.fullmatch(r"([^:@\s]+):([^@\s]*)@(.+):(\d{1,5})", line)
    if auth_match:
        return {
            "scheme": default_scheme,
            "username": auth_match.group(1),
            "password": auth_match.group(2),
            "host": auth_match.group(3),
            "port": auth_match.group(4),
        }

    # host:port:user:pass (common provider export; IPv6 should use URI or JSON)
    parts = line.split(":")
    if len(parts) == 4 and parts[1].isdigit():
        return {
            "scheme": default_scheme,
            "host": parts[0],
            "port": parts[1],
            "username": parts[2],
            "password": parts[3],
        }

    # host:port
    host_port = re.fullmatch(r"(\[[^\]]+\]|[^:\s]+):(\d{1,5})", line)
    if host_port:
        return {
            "scheme": default_scheme,
            "host": host_port.group(1).strip("[]"),
            "port": host_port.group(2),
        }
    return None


def _mapping_from_columns(parts: list[str], default_scheme: str) -> dict:
    if len(parts) == 2:
        if "://" in parts[1]:
            return {"name": parts[0], "proxy_url": parts[1]}
        return {"scheme": default_scheme, "host": parts[0], "port": parts[1]}
    if len(parts) == 3:
        if "://" in parts[1]:
            return {"name": parts[0], "proxy_url": parts[1], "rotate_api_url": parts[2]}
        return {"name": parts[0], "scheme": default_scheme, "host": parts[1], "port": parts[2]}
    if len(parts) == 4:
        return {
            "scheme": default_scheme,
            "host": parts[0],
            "port": parts[1],
            "username": parts[2],
            "password": parts[3],
        }
    if len(parts) == 5:
        return {
            "name": parts[0],
            "scheme": default_scheme,
            "host": parts[1],
            "port": parts[2],
            "username": parts[3],
            "password": parts[4],
        }
    if len(parts) >= 6:
        mapping = {
            "name": parts[0],
            "scheme": parts[1] or default_scheme,
            "host": parts[2],
            "port": parts[3],
            "username": parts[4],
            "password": parts[5],
        }
        if len(parts) >= 7 and parts[6]:
            mapping["rotate_api_url"] = parts[6]
        if len(parts) >= 8 and parts[7]:
            mapping["rotate_api_method"] = parts[7]
        if len(parts) >= 9 and parts[8]:
            mapping["rotate_wait_seconds"] = parts[8]
        return mapping
    raise ProxyImportError("无法识别该行格式")


def _parse_non_json_lines(text: str, default_scheme: str, prefix: str) -> list[ProxyImportItem]:
    items: list[ProxyImportItem] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        index = len(items) + 1
        try:
            if line.startswith("{"):
                loaded = json.loads(line)
                if not isinstance(loaded, dict):
                    raise ProxyImportError("JSON 行必须是对象")
                item = _item_from_mapping(loaded, index, prefix, line_number)
            elif "://" in line and not _split_delimited(line):
                item = ProxyImportItem(
                    line_number=line_number,
                    name=f"{prefix}-{index}",
                    proxy_url=normalize_proxy_url(line),
                )
            else:
                parts = _split_delimited(line)
                mapping = _mapping_from_columns(parts, default_scheme) if parts else _parse_colon_format(line, default_scheme)
                if not mapping:
                    raise ProxyImportError("无法识别该行格式")
                item = _item_from_mapping(mapping, index, prefix, line_number)
        except (ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, ProxyImportError):
                raise ProxyImportError(f"第 {line_number} 行：{exc}") from exc
            raise ProxyImportError(f"第 {line_number} 行：{exc}") from exc
        items.append(item)
        if len(items) > MAX_IMPORT_ITEMS:
            raise ProxyImportError(f"一次最多导入 {MAX_IMPORT_ITEMS} 个代理")
    return items


def _parse_json(text: str, prefix: str) -> list[ProxyImportItem]:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProxyImportError(f"JSON 格式错误：第 {exc.lineno} 行 {exc.msg}") from exc
    if isinstance(loaded, dict) and isinstance(loaded.get("proxies"), list):
        loaded = loaded["proxies"]
    if isinstance(loaded, dict):
        loaded = [loaded]
    if not isinstance(loaded, list):
        raise ProxyImportError("JSON 必须是代理对象、对象数组或包含 proxies 数组的对象")
    if len(loaded) > MAX_IMPORT_ITEMS:
        raise ProxyImportError(f"一次最多导入 {MAX_IMPORT_ITEMS} 个代理")
    items = []
    for index, entry in enumerate(loaded, start=1):
        if not isinstance(entry, dict):
            raise ProxyImportError(f"JSON 第 {index} 项必须是对象")
        try:
            items.append(_item_from_mapping(entry, index, prefix, index))
        except ValueError as exc:
            raise ProxyImportError(f"JSON 第 {index} 项：{exc}") from exc
    return items


def _deduplicate_names(items: list[ProxyImportItem]) -> None:
    used: set[str] = set()
    for item in items:
        base = item.name
        candidate = base
        number = 2
        while candidate.casefold() in used:
            candidate = f"{base}-{number}"
            number += 1
        item.name = candidate
        used.add(candidate.casefold())


def parse_proxy_import(
    text: str,
    *,
    default_scheme: str = "socks5",
    name_prefix: str = "代理",
) -> list[ProxyImportItem]:
    raw = str(text or "")
    if not raw.strip():
        raise ProxyImportError("请粘贴需要导入的代理")
    if len(raw.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ProxyImportError("导入内容不能超过 512 KiB")
    scheme = str(default_scheme or "socks5").strip().lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        raise ProxyImportError("默认协议不受支持")
    prefix = str(name_prefix or "代理").strip() or "代理"
    if len(prefix) > 80:
        raise ProxyImportError("名称前缀不能超过 80 个字符")
    stripped = raw.lstrip("\ufeff \t\r\n")
    if stripped.startswith(("[", "{")):
        try:
            items = _parse_json(raw, prefix)
        except ProxyImportError:
            if stripped.startswith("{"):
                items = _parse_non_json_lines(raw, scheme, prefix)
            else:
                raise
    else:
        items = _parse_non_json_lines(raw, scheme, prefix)
    if not items:
        raise ProxyImportError("没有找到可导入的代理")
    _deduplicate_names(items)
    return items
