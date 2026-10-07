from __future__ import annotations

from copy import deepcopy
from typing import Any

from .launch_service import (
    load_launch_catalog,
    load_launch_resources,
    normalize_architecture,
    shape_for_architecture,
)

# These values belong to one OCI tenant/catalog snapshot and must never be
# copied verbatim to another tenant. The rest of the launch policy remains a
# reusable template and is resolved by an explicit user-triggered preflight.
TENANT_BOUND_PROFILE_FIELDS = {
    "catalog_token",
    "compartment_id",
    "network_compartment_id",
    "availability_domain",
    "subnet_id",
    "image_id",
    "region",
    "image_name",
    "subnet_name",
}


def launch_profile_template(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return a cross-tenant-safe copy of a saved launch payload."""
    result = deepcopy(payload or {})
    for key in TENANT_BOUND_PROFILE_FIELDS:
        result.pop(key, None)
    architecture = normalize_architecture(result.get("architecture") or "ARM")
    result["architecture"] = architecture
    # Shape is deterministic for the supported architecture. Keep it visible
    # in the copied profile while the live preflight verifies availability.
    result["shape"] = shape_for_architecture(architecture)
    return result


def _check(
    key: str,
    label: str,
    status: str,
    value: Any,
    message: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "value": value,
        "message": message,
    }


def _find_by_id(items: list[dict], item_id: str | None) -> dict | None:
    normalized = str(item_id or "").strip()
    if not normalized:
        return None
    return next(
        (item for item in items if str(item.get("id") or "").strip() == normalized),
        None,
    )


def preflight_launch_payload(
    account_id: int,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve and verify a saved profile against current OCI resources.

    This function performs live OCI requests and must only be called from an
    explicit user action such as preflight, run or retry-failed.
    """
    original = deepcopy(payload or {})
    normalized = deepcopy(original)
    checks: list[dict[str, Any]] = []
    changed_fields: list[str] = []

    architecture = normalize_architecture(original.get("architecture") or "ARM")
    normalized["architecture"] = architecture
    expected_shape = shape_for_architecture(architecture)

    catalog = load_launch_catalog(int(account_id))
    compartments = list(catalog.get("compartments") or [])
    availability_domains = list(catalog.get("availability_domains") or [])
    if not compartments:
        raise ValueError("当前租户没有可访问的实例区间")
    if not availability_domains:
        raise ValueError("当前租户没有可用域")

    saved_compartment = str(original.get("compartment_id") or "").strip()
    compartment = _find_by_id(compartments, saved_compartment) or compartments[0]
    compartment_id = str(compartment.get("id") or "").strip()
    if not compartment_id:
        raise ValueError("当前租户目录缺少有效的实例区间 OCID")
    checks.append(
        _check(
            "compartment",
            "实例区间",
            "ok" if compartment_id == saved_compartment else "updated",
            compartment_id,
            (
                "原配置区间仍可用"
                if compartment_id == saved_compartment
                else f"已自动改为 {compartment.get('name') or compartment_id}"
            ),
        )
    )

    saved_network_compartment = str(
        original.get("network_compartment_id") or ""
    ).strip()
    network_compartment = (
        _find_by_id(compartments, saved_network_compartment) or compartment
    )
    network_compartment_id = str(network_compartment.get("id") or compartment_id)

    saved_ad = str(original.get("availability_domain") or "").strip()
    availability_domain = (
        saved_ad if saved_ad in availability_domains else availability_domains[0]
    )
    checks.append(
        _check(
            "availability_domain",
            "可用域",
            "ok" if availability_domain == saved_ad else "updated",
            availability_domain,
            "原配置可用域仍可用" if availability_domain == saved_ad else "已自动选择当前可用域",
        )
    )

    resources = load_launch_resources(
        int(account_id),
        catalog_token=str(catalog["catalog_token"]),
        compartment_id=compartment_id,
        availability_domain=availability_domain,
        architecture=architecture,
        network_compartment_id=network_compartment_id,
    )

    actual_shape = str(resources.get("shape") or "").strip()
    shape_ok = actual_shape == expected_shape
    checks.append(
        _check(
            "shape",
            "Shape",
            "ok" if shape_ok else "error",
            actual_shape or expected_shape,
            (
                f"当前可用域支持 {expected_shape}"
                if shape_ok
                else str(resources.get("shape_error") or f"当前可用域不支持 {expected_shape}")
            ),
        )
    )

    subnets = list(resources.get("subnets") or [])
    saved_subnet = str(original.get("subnet_id") or "").strip()
    selected_subnet = _find_by_id(subnets, saved_subnet)
    if selected_subnet is None:
        recommended_id = str(resources.get("recommended_subnet_id") or "").strip()
        selected_subnet = _find_by_id(subnets, recommended_id)
    if selected_subnet is None and subnets:
        selected_subnet = subnets[0]
    subnet_id = str((selected_subnet or {}).get("id") or "").strip()
    subnet_ok = bool(subnet_id)
    checks.append(
        _check(
            "subnet",
            "子网",
            (
                "ok"
                if subnet_ok and subnet_id == saved_subnet
                else "updated" if subnet_ok else "error"
            ),
            subnet_id or None,
            (
                "原配置子网仍可用"
                if subnet_ok and subnet_id == saved_subnet
                else f"已自动选择 {(selected_subnet or {}).get('display_name') or subnet_id}"
                if subnet_ok
                else "当前租户没有可用子网，请先创建 N&T 网络"
            ),
        )
    )

    images = list(resources.get("images") or [])
    saved_image = str(original.get("image_id") or "").strip()
    selected_image = _find_by_id(images, saved_image) or (images[0] if images else None)
    image_id = str((selected_image or {}).get("id") or "").strip()
    image_ok = bool(image_id)
    checks.append(
        _check(
            "image",
            "Ubuntu 镜像",
            (
                "ok"
                if image_ok and image_id == saved_image
                else "updated" if image_ok else "error"
            ),
            image_id or None,
            (
                "原配置镜像仍可用"
                if image_ok and image_id == saved_image
                else f"已自动选择 {(selected_image or {}).get('display_name') or image_id}"
                if image_ok
                else str(resources.get("image_error") or "没有兼容的 Ubuntu 镜像")
            ),
        )
    )

    region = str(catalog.get("region") or "").strip()
    saved_region = str(original.get("region") or "").strip()
    checks.insert(
        0,
        _check(
            "region",
            "区域",
            "ok" if saved_region == region else "updated",
            region,
            "区域保持不变" if saved_region == region else "已使用目标租户当前主区域",
        ),
    )
    checks.insert(
        1,
        _check(
            "architecture",
            "架构",
            "ok",
            architecture,
            f"使用 {architecture} 架构",
        ),
    )

    resolved = {
        "catalog_token": str(catalog["catalog_token"]),
        "compartment_id": compartment_id,
        "network_compartment_id": str(
            (selected_subnet or {}).get("compartment_id") or network_compartment_id
        ),
        "availability_domain": availability_domain,
        "subnet_id": subnet_id,
        "image_id": image_id,
        "region": region,
        "architecture": architecture,
        "shape": actual_shape or expected_shape,
    }
    if selected_image:
        resolved["image_name"] = selected_image.get("display_name")
    if selected_subnet:
        resolved["subnet_name"] = selected_subnet.get("display_name")

    for key, value in resolved.items():
        if normalized.get(key) != value:
            changed_fields.append(key)
        normalized[key] = value

    ok = shape_ok and subnet_ok and image_ok
    errors = [item["message"] for item in checks if item["status"] == "error"]
    return {
        "ok": ok,
        "account_id": int(account_id),
        "region": region,
        "architecture": architecture,
        "shape": actual_shape or expected_shape,
        "checks": checks,
        "changed_fields": changed_fields,
        "payload": normalized,
        "error": "；".join(errors) if errors else None,
    }


def require_preflight_launch_payload(
    account_id: int,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    result = preflight_launch_payload(account_id, payload)
    if not result["ok"]:
        raise ValueError(result.get("error") or "开机资源预检未通过")
    return result
