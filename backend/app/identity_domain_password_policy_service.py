from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import oci

from .launch_service import get_client_bundle, identity_client
from .proxy_service import apply_proxy_to_oci_client


PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

PATCH_FIELDS = {
    "password_strength": "passwordStrength",
    "min_length": "minLength",
    "max_length": "maxLength",
    "password_expires_after": "passwordExpiresAfter",
    "password_expire_warning": "passwordExpireWarning",
    "max_incorrect_attempts": "maxIncorrectAttempts",
    "num_passwords_in_history": "numPasswordsInHistory",
    "min_alphas": "minAlphas",
    "min_numerals": "minNumerals",
    "min_special_chars": "minSpecialChars",
    "min_upper_case": "minUpperCase",
    "min_lower_case": "minLowerCase",
    "min_unique_chars": "minUniqueChars",
    "max_repeated_chars": "maxRepeatedChars",
    "starts_with_alphabet": "startsWithAlphabet",
    "first_name_disallowed": "firstNameDisallowed",
    "last_name_disallowed": "lastNameDisallowed",
    "user_name_disallowed": "userNameDisallowed",
    "dictionary_word_disallowed": "dictionaryWordDisallowed",
}

INTEGER_FIELDS = {
    "min_length",
    "max_length",
    "password_expires_after",
    "password_expire_warning",
    "max_incorrect_attempts",
    "num_passwords_in_history",
    "min_alphas",
    "min_numerals",
    "min_special_chars",
    "min_upper_case",
    "min_lower_case",
    "min_unique_chars",
    "max_repeated_chars",
}

BOOLEAN_FIELDS = {
    "starts_with_alphabet",
    "first_name_disallowed",
    "last_name_disallowed",
    "user_name_disallowed",
    "dictionary_word_disallowed",
}


def _none_retry():
    return oci.retry.NoneRetryStrategy()


def _value(obj: Any, name: str, default=None):
    return getattr(obj, name, default) if obj is not None else default


def _domain_endpoint(domain: Any) -> str:
    for name in ("url", "home_region_url"):
        value = str(_value(domain, name, "") or "").strip().rstrip("/")
        if value:
            return value
    raise ValueError("Identity Domain 缺少可用服务地址")


def _domain_client(config: dict, proxy_url: str | None, endpoint: str):
    client = oci.identity_domains.IdentityDomainsClient(
        config,
        service_endpoint=endpoint,
    )
    if proxy_url:
        apply_proxy_to_oci_client(client, proxy_url)
    return client


def _password_policy_resources(client) -> list[Any]:
    response = client.list_password_policies(
        attribute_sets=["all"],
        retry_strategy=_none_retry(),
    )
    data = response.data
    resources = getattr(data, "resources", None)
    if resources is None and isinstance(data, list):
        resources = data
    return list(resources or [])


def _normalize_policy(policy: Any) -> dict:
    prevented = [
        str(item)
        for item in list(_value(policy, "idcs_prevented_operations", None) or [])
    ]
    lowered = {item.lower() for item in prevented}
    return {
        "id": str(_value(policy, "id", "") or ""),
        "ocid": str(_value(policy, "ocid", "") or ""),
        "name": str(_value(policy, "name", "") or ""),
        "description": str(_value(policy, "description", "") or ""),
        "priority": _value(policy, "priority"),
        "password_strength": str(_value(policy, "password_strength", "") or ""),
        "min_length": _value(policy, "min_length"),
        "max_length": _value(policy, "max_length"),
        "password_expires_after": _value(policy, "password_expires_after"),
        "password_expire_warning": _value(policy, "password_expire_warning"),
        "max_incorrect_attempts": _value(policy, "max_incorrect_attempts"),
        "num_passwords_in_history": _value(policy, "num_passwords_in_history"),
        "min_alphas": _value(policy, "min_alphas"),
        "min_numerals": _value(policy, "min_numerals"),
        "min_special_chars": _value(policy, "min_special_chars"),
        "min_upper_case": _value(policy, "min_upper_case"),
        "min_lower_case": _value(policy, "min_lower_case"),
        "min_unique_chars": _value(policy, "min_unique_chars"),
        "max_repeated_chars": _value(policy, "max_repeated_chars"),
        "starts_with_alphabet": bool(_value(policy, "starts_with_alphabet", False)),
        "first_name_disallowed": bool(_value(policy, "first_name_disallowed", False)),
        "last_name_disallowed": bool(_value(policy, "last_name_disallowed", False)),
        "user_name_disallowed": bool(_value(policy, "user_name_disallowed", False)),
        "dictionary_word_disallowed": bool(_value(policy, "dictionary_word_disallowed", False)),
        "idcs_prevented_operations": prevented,
        "update_allowed": "update" not in lowered and "replace" not in lowered,
    }


def _discover(account_id: int):
    _account, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    tenancy_id = str(config.get("tenancy") or "").strip()
    if not tenancy_id:
        raise ValueError("当前 OCI 配置缺少 tenancy OCID")

    domains = oci.pagination.list_call_get_all_results(
        identity.list_domains,
        tenancy_id,
        retry_strategy=_none_retry(),
    ).data

    active = [
        domain
        for domain in list(domains or [])
        if str(_value(domain, "lifecycle_state", "") or "").upper() == "ACTIVE"
    ]
    active.sort(
        key=lambda item: (
            0 if str(_value(item, "type", "") or "").upper() == "DEFAULT" else 1,
            str(_value(item, "display_name", "") or "").lower(),
        )
    )
    return config, proxy_url, active


def list_identity_domain_password_policies(account_id: int) -> dict:
    config, proxy_url, domains = _discover(account_id)
    result: list[dict] = []

    for domain in domains:
        item = {
            "id": str(_value(domain, "id", "") or ""),
            "display_name": str(_value(domain, "display_name", "") or ""),
            "type": str(_value(domain, "type", "") or ""),
            "lifecycle_state": str(_value(domain, "lifecycle_state", "") or ""),
            "url": str(_value(domain, "url", "") or ""),
            "home_region_url": str(_value(domain, "home_region_url", "") or ""),
            "policies": [],
            "error": None,
        }
        try:
            endpoint = _domain_endpoint(domain)
            client = _domain_client(config, proxy_url, endpoint)
            item["policies"] = [
                _normalize_policy(policy)
                for policy in _password_policy_resources(client)
            ]
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        result.append(item)

    return {
        "account_id": int(account_id),
        "read_at": datetime.now(timezone.utc).isoformat(),
        "domains": result,
    }


def _coerce_patch_value(field: str, value: Any):
    if field == "password_strength":
        normalized = str(value or "").strip().title()
        if normalized not in {"Simple", "Standard", "Custom"}:
            raise ValueError("密码策略类型只允许 Simple、Standard 或 Custom")
        return normalized

    if field in INTEGER_FIELDS:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 必须是整数") from exc
        if number < 0:
            raise ValueError(f"{field} 不能小于 0")
        if field in {"min_length", "max_length"} and number > 500:
            raise ValueError("密码长度不能大于 500")
        return number

    if field in BOOLEAN_FIELDS:
        if isinstance(value, bool):
            return value
        if value in (0, 1, "0", "1"):
            return bool(int(value))
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ValueError(f"{field} 必须是布尔值")

    return value


def update_identity_domain_password_policy(
    account_id: int,
    domain_id: str,
    policy_id: str,
    payload: dict,
) -> dict:
    domain_id = str(domain_id or "").strip()
    policy_id = str(policy_id or "").strip()
    if not domain_id or not policy_id:
        raise ValueError("Identity Domain 或密码策略 ID 为空")
    if not isinstance(payload, dict):
        raise ValueError("密码策略提交内容无效")

    config, proxy_url, domains = _discover(account_id)
    domain = next(
        (item for item in domains if str(_value(item, "id", "") or "") == domain_id),
        None,
    )
    if domain is None:
        raise KeyError("Identity Domain 不存在、未激活或不属于当前租户")

    client = _domain_client(config, proxy_url, _domain_endpoint(domain))
    policies = _password_policy_resources(client)
    policy = next(
        (item for item in policies if str(_value(item, "id", "") or "") == policy_id),
        None,
    )
    if policy is None:
        raise KeyError("Identity Domain 密码策略不存在")

    current = _normalize_policy(policy)
    if not current["update_allowed"]:
        raise ValueError("Oracle 标记该密码策略为不可更新")

    fields: dict[str, Any] = {}
    for field, value in payload.items():
        if field not in PATCH_FIELDS:
            continue
        fields[field] = _coerce_patch_value(field, value)

    if not fields:
        raise ValueError("没有可更新的密码策略字段")

    requested_strength = fields.get(
        "password_strength",
        current.get("password_strength") or "Custom",
    )
    if requested_strength != "Custom":
        fields = {"password_strength": requested_strength}
    else:
        fields["password_strength"] = "Custom"

    min_length = fields.get("min_length", current.get("min_length"))
    max_length = fields.get("max_length", current.get("max_length"))
    if min_length is not None and max_length is not None and int(min_length) > int(max_length):
        raise ValueError("最小密码长度不能大于最大密码长度")

    operations = [
        oci.identity_domains.models.Operations(
            op="replace",
            path=PATCH_FIELDS[field],
            value=value,
        )
        for field, value in fields.items()
    ]
    patch = oci.identity_domains.models.PatchOp(
        schemas=[PATCH_SCHEMA],
        operations=operations,
    )

    client.patch_password_policy(
        policy_id,
        patch,
        retry_strategy=_none_retry(),
    )

    refreshed = _password_policy_resources(client)
    updated = next(
        (item for item in refreshed if str(_value(item, "id", "") or "") == policy_id),
        None,
    )
    if updated is None:
        raise RuntimeError("密码策略已提交，但重新读取结果中未找到该策略")
    return _normalize_policy(updated)
