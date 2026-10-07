from datetime import datetime, timedelta, timezone
from typing import Any
import base64
import ipaddress
import json
import uuid

import oci

from .credential_crypto import decrypt_secret
from .launch_repository import (
    create_catalog_snapshot,
    get_catalog_resources,
    get_catalog_snapshot,
    save_catalog_resources,
)
from .oci_service import (
    compute_client,
    get_client_bundle,
    identity_client,
    list_accessible_compartments,
    network_client,
)
from .region_names import get_region_display_name

CATALOG_TTL = timedelta(hours=2)

SUPPORTED_LAUNCH_SHAPES = {
    "ARM": "VM.Standard.A1.Flex",
    "AMD": "VM.Standard.E2.1.Micro",
}

AUTO_VCN_CIDRS = tuple(f"10.{second}.0.0/16" for second in range(88, 100))
AUTO_NETWORK_VCN_NAME = "N&T-VCN"
AUTO_NETWORK_SUBNET_NAME = "N&T-Subnet"
AUTO_NETWORK_GATEWAY_NAME = "N&T-IGW"


def normalize_architecture(value: str | None) -> str:
    architecture = str(value or "").strip().upper()
    if architecture not in SUPPORTED_LAUNCH_SHAPES:
        raise ValueError("抢机架构只允许 ARM 或 AMD")
    return architecture


def shape_for_architecture(architecture: str) -> str:
    return SUPPORTED_LAUNCH_SHAPES[normalize_architecture(architecture)]


def _is_ubuntu_image(item: Any) -> bool:
    operating_system = str(getattr(item, "operating_system", "") or "").lower()
    display_name = str(getattr(item, "display_name", "") or "").lower()
    return "ubuntu" in operating_system or "ubuntu" in display_name



# OCI-N&T V1.0.4 1.0.4-launch-key-list1: launch image fallback + public IP
def _image_matches_architecture(item: Any, architecture: str) -> bool:
    # Shape-filtered queries remain preferred. This helper protects the
    # no-shape fallback from mixing ARM and x86 Ubuntu platform images.
    architecture = normalize_architecture(architecture)
    text = " ".join(
        str(value or "")
        for value in (
            getattr(item, "display_name", None),
            getattr(item, "operating_system", None),
            getattr(item, "operating_system_version", None),
        )
    ).lower()
    arm_markers = ("aarch64", "arm64", "arm-based", " arm ")
    is_arm = any(marker in text for marker in arm_markers)
    if architecture == "ARM":
        return is_arm
    if "x86_64" in text or "amd64" in text or "x86-64" in text:
        return True
    return not is_arm


def _none_retry():
    return oci.retry.NoneRetryStrategy()


def _to_dict(value: Any) -> Any:
    return oci.util.to_dict(value) if value is not None else None


def _snapshot_or_error(token: str, account_id: int, *, allow_expired: bool = False) -> dict:
    snapshot = get_catalog_snapshot(token)
    if not snapshot or int(snapshot["account_id"]) != int(account_id):
        raise ValueError("创建目录无效或不属于当前租户，请重新读取配置")
    try:
        created_at = datetime.fromisoformat(str(snapshot["created_at"]))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise ValueError("创建目录时间无效，请重新读取配置") from None
    if not allow_expired and datetime.now(timezone.utc) - created_at > CATALOG_TTL:
        raise ValueError("创建目录已过期，请重新读取配置")
    return snapshot


def load_launch_catalog(account_id: int) -> dict:
    account, config, proxy_url = get_client_bundle(account_id)
    region = str(config["region"]).strip().lower()
    identity = identity_client(config, proxy_url)
    compartments = list_accessible_compartments(
        config,
        proxy_url,
        account["tenancy_ocid"],
    )
    availability_domains = oci.pagination.list_call_get_all_results(
        identity.list_availability_domains,
        account["tenancy_ocid"],
        retry_strategy=_none_retry(),
    ).data
    ad_names = [
        str(item.name)
        for item in availability_domains
        if getattr(item, "name", None)
    ]
    token = create_catalog_snapshot(
        account_id=account_id,
        region=region,
        compartment_ids=[item["id"] for item in compartments],
        availability_domains=ad_names,
    )
    return {
        "catalog_token": token,
        "account_id": account_id,
        "region": region,
        "region_label": get_region_display_name(region),
        "compartments": compartments,
        "availability_domains": ad_names,
        "expires_in_seconds": int(CATALOG_TTL.total_seconds()),
    }


def _list_available_subnets(network, compartment_id: str) -> list[Any]:
    """List available subnets from one compartment without hiding OCI errors."""
    return oci.pagination.list_call_get_all_results(
        network.list_subnets,
        compartment_id,
        lifecycle_state="AVAILABLE",
        retry_strategy=_none_retry(),
    ).data


def _subnet_item(item: Any, network=None) -> dict:
    ipv6_blocks = list(getattr(item, "ipv6_cidr_blocks", None) or [])
    legacy_ipv6 = getattr(item, "ipv6_cidr_block", None)
    if legacy_ipv6 and legacy_ipv6 not in ipv6_blocks:
        ipv6_blocks.append(legacy_ipv6)
    result = {
        "id": item.id,
        "display_name": item.display_name,
        "cidr_block": item.cidr_block,
        "prohibit_public_ip_on_vnic": bool(
            getattr(item, "prohibit_public_ip_on_vnic", False)
        ),
        "prohibit_internet_ingress": bool(
            getattr(item, "prohibit_internet_ingress", False)
        ),
        "vcn_id": item.vcn_id,
        "compartment_id": getattr(item, "compartment_id", None),
        "availability_domain": getattr(item, "availability_domain", None),
        "ipv6_cidr_blocks": ipv6_blocks,
        "has_ipv6": bool(ipv6_blocks),
        "route_table_id": getattr(item, "route_table_id", None),
        "has_ipv4_internet_route": False,
        "has_ipv6_internet_route": False,
        "route_error": None,
    }
    if network is not None:
        try:
            route_table_id = result["route_table_id"]
            if not route_table_id:
                vcn = network.get_vcn(
                    item.vcn_id,
                    retry_strategy=_none_retry(),
                ).data
                route_table_id = vcn.default_route_table_id
                result["route_table_id"] = route_table_id
            route_table = network.get_route_table(
                route_table_id,
                retry_strategy=_none_retry(),
            ).data
            for rule in list(getattr(route_table, "route_rules", None) or []):
                destination = str(
                    getattr(rule, "destination", None)
                    or getattr(rule, "cidr_block", None)
                    or ""
                )
                entity = str(getattr(rule, "network_entity_id", None) or "")
                if not entity.startswith("ocid1.internetgateway."):
                    continue
                if destination == "0.0.0.0/0":
                    result["has_ipv4_internet_route"] = True
                elif destination == "::/0":
                    result["has_ipv6_internet_route"] = True
        except Exception as exc:
            result["route_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _recommended_public_subnet(items: list[dict]) -> dict | None:
    return next((
        item for item in items
        if not item.get("prohibit_public_ip_on_vnic")
        and item.get("has_ipv4_internet_route")
    ), None)


def _subnet_rank(item: dict, preferred_compartment_id: str) -> tuple:
    # Prefer a public-capable regional subnet in the selected/root compartment.
    return (
        not bool(item.get("has_ipv4_internet_route")),
        bool(item.get("prohibit_public_ip_on_vnic")),
        str(item.get("compartment_id") or "") != preferred_compartment_id,
        item.get("availability_domain") is not None,
        str(item.get("display_name") or "").lower(),
        str(item.get("cidr_block") or ""),
    )


def _collect_available_subnets(
    network,
    compartment_ids: list[str],
    preferred_compartment_id: str,
) -> tuple[list[dict], list[dict]]:
    ordered = [preferred_compartment_id] + [
        item for item in compartment_ids if item != preferred_compartment_id
    ]
    subnet_items: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()
    for compartment_id in ordered:
        try:
            rows = _list_available_subnets(network, compartment_id)
        except Exception as exc:
            errors.append({
                "compartment_id": compartment_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        for row in rows:
            subnet_id = str(getattr(row, "id", "") or "")
            if not subnet_id or subnet_id in seen:
                continue
            seen.add(subnet_id)
            subnet_items.append(_subnet_item(row, network))
    subnet_items.sort(key=lambda item: _subnet_rank(item, preferred_compartment_id))
    return subnet_items, errors


def _list_all_vcns(network, compartment_ids: list[str]) -> list[Any]:
    rows: list[Any] = []
    seen: set[str] = set()
    for compartment_id in compartment_ids:
        try:
            items = oci.pagination.list_call_get_all_results(
                network.list_vcns,
                compartment_id,
                lifecycle_state="AVAILABLE",
                retry_strategy=_none_retry(),
            ).data
        except Exception:
            continue
        for item in items:
            item_id = str(getattr(item, "id", "") or "")
            if item_id and item_id not in seen:
                seen.add(item_id)
                rows.append(item)
    return rows


def _select_auto_cidr(vcns: list[Any]) -> tuple[str, str]:
    existing: list[ipaddress.IPv4Network] = []
    for vcn in vcns:
        values = list(getattr(vcn, "cidr_blocks", None) or [])
        legacy = getattr(vcn, "cidr_block", None)
        if legacy and legacy not in values:
            values.append(legacy)
        for value in values:
            try:
                network = ipaddress.ip_network(str(value), strict=False)
            except ValueError:
                continue
            if isinstance(network, ipaddress.IPv4Network):
                existing.append(network)
    for vcn_cidr in AUTO_VCN_CIDRS:
        candidate = ipaddress.ip_network(vcn_cidr)
        if any(candidate.overlaps(current) for current in existing):
            continue
        subnet = next(candidate.subnets(new_prefix=24))
        return str(candidate), str(subnet)
    raise ValueError("10.88.0.0/16 至 10.99.0.0/16 均与现有 VCN 冲突，请手动整理网络")


def _first_ipv6_subnet(vcn: Any) -> str | None:
    values = list(getattr(vcn, "ipv6_cidr_blocks", None) or [])
    legacy = getattr(vcn, "ipv6_cidr_block", None)
    if legacy and legacy not in values:
        values.append(legacy)
    for value in values:
        try:
            network = ipaddress.ip_network(str(value), strict=False)
        except ValueError:
            continue
        if isinstance(network, ipaddress.IPv6Network):
            if network.prefixlen == 64:
                return str(network)
            return str(next(network.subnets(new_prefix=64)))
    return None


def ensure_launch_network(
    account_id: int,
    *,
    catalog_token: str,
    compartment_id: str,
    availability_domain: str,
) -> dict:
    """Return an existing launch subnet or create a minimal N&T public network."""
    snapshot = _snapshot_or_error(catalog_token, account_id)
    if compartment_id not in snapshot["compartment_ids"]:
        raise ValueError("自动网络区间不属于当前租户目录")
    if availability_domain not in snapshot["availability_domains"]:
        raise ValueError("可用域不属于当前租户目录")

    _, config, proxy_url = get_client_bundle(account_id, snapshot["region"])
    network = network_client(config, proxy_url)
    subnet_items, subnet_errors = _collect_available_subnets(
        network,
        snapshot["compartment_ids"],
        compartment_id,
    )
    recommended_existing = _recommended_public_subnet(subnet_items)
    if recommended_existing:
        return {
            "created": False,
            "message": "当前租户已经存在可直连公网的子网，已自动选用，不重复创建网络",
            "subnet": recommended_existing,
            "subnet_errors": subnet_errors,
        }

    composite = oci.core.VirtualNetworkClientCompositeOperations(network)
    vcns = _list_all_vcns(network, snapshot["compartment_ids"])
    vcn_cidr, subnet_cidr = _select_auto_cidr(vcns)
    existing_names = {
        str(getattr(item, "display_name", "") or "") for item in vcns
    }
    suffix = 1
    vcn_name = AUTO_NETWORK_VCN_NAME
    while vcn_name in existing_names:
        suffix += 1
        vcn_name = f"{AUTO_NETWORK_VCN_NAME}-{suffix}"
    subnet_name = AUTO_NETWORK_SUBNET_NAME if suffix == 1 else f"{AUTO_NETWORK_SUBNET_NAME}-{suffix}"
    gateway_name = AUTO_NETWORK_GATEWAY_NAME if suffix == 1 else f"{AUTO_NETWORK_GATEWAY_NAME}-{suffix}"

    vcn = gateway = subnet = None
    route_table_id = None
    original_route_rules: list[Any] | None = None
    stage = "创建 VCN"
    cleanup_errors: list[str] = []
    try:
        vcn_details = oci.core.models.CreateVcnDetails(
            compartment_id=compartment_id,
            cidr_blocks=[vcn_cidr],
            display_name=vcn_name,
            dns_label=f"nt{uuid.uuid4().hex[:10]}",
            is_ipv6_enabled=True,
        )
        vcn = composite.create_vcn_and_wait_for_state(
            vcn_details,
            wait_for_states=[oci.core.models.Vcn.LIFECYCLE_STATE_AVAILABLE],
        ).data

        stage = "创建 Internet Gateway"
        gateway_details = oci.core.models.CreateInternetGatewayDetails(
            compartment_id=compartment_id,
            vcn_id=vcn.id,
            display_name=gateway_name,
            is_enabled=True,
        )
        gateway = composite.create_internet_gateway_and_wait_for_state(
            gateway_details,
            wait_for_states=[
                oci.core.models.InternetGateway.LIFECYCLE_STATE_AVAILABLE
            ],
        ).data

        stage = "配置默认路由"
        route_table_id = vcn.default_route_table_id
        route_table = network.get_route_table(
            route_table_id,
            retry_strategy=_none_retry(),
        ).data
        original_route_rules = list(getattr(route_table, "route_rules", None) or [])
        route_rules = list(original_route_rules)
        route_rules.append(oci.core.models.RouteRule(
            destination="0.0.0.0/0",
            destination_type="CIDR_BLOCK",
            network_entity_id=gateway.id,
            description="OCI-N&T automatic IPv4 internet route",
        ))
        ipv6_subnet = _first_ipv6_subnet(vcn)
        if ipv6_subnet:
            route_rules.append(oci.core.models.RouteRule(
                destination="::/0",
                destination_type="CIDR_BLOCK",
                network_entity_id=gateway.id,
                description="OCI-N&T automatic IPv6 internet route",
            ))
        network.update_route_table(
            route_table_id,
            oci.core.models.UpdateRouteTableDetails(route_rules=route_rules),
            retry_strategy=_none_retry(),
        )

        stage = "创建区域子网"
        subnet_details = oci.core.models.CreateSubnetDetails(
            compartment_id=compartment_id,
            vcn_id=vcn.id,
            display_name=subnet_name,
            dns_label=f"sn{uuid.uuid4().hex[:10]}",
            cidr_block=subnet_cidr,
            ipv6_cidr_block=ipv6_subnet,
            prohibit_internet_ingress=False,
            route_table_id=route_table_id,
        )
        subnet = composite.create_subnet_and_wait_for_state(
            subnet_details,
            wait_for_states=[oci.core.models.Subnet.LIFECYCLE_STATE_AVAILABLE],
        ).data
        item = _subnet_item(subnet, network)
        return {
            "created": True,
            "message": "N&T 网络已创建并自动选中",
            "vcn": {
                "id": vcn.id,
                "display_name": vcn.display_name,
                "cidr_block": vcn_cidr,
                "ipv6_cidr_blocks": list(
                    getattr(vcn, "ipv6_cidr_blocks", None) or []
                ),
            },
            "internet_gateway": {
                "id": gateway.id,
                "display_name": gateway.display_name,
            },
            "subnet": item,
            "subnet_errors": subnet_errors,
        }
    except Exception as exc:
        # Best-effort rollback prevents a failed wizard from leaving a half-built VCN.
        if subnet is not None:
            try:
                composite.delete_subnet_and_wait_for_state(
                    subnet.id,
                    wait_for_states=[
                        oci.core.models.Subnet.LIFECYCLE_STATE_TERMINATED
                    ],
                )
            except Exception as cleanup_exc:
                cleanup_errors.append(f"删除子网失败：{cleanup_exc}")
        if route_table_id and original_route_rules is not None:
            try:
                network.update_route_table(
                    route_table_id,
                    oci.core.models.UpdateRouteTableDetails(
                        route_rules=original_route_rules
                    ),
                    retry_strategy=_none_retry(),
                )
            except Exception as cleanup_exc:
                cleanup_errors.append(f"恢复路由表失败：{cleanup_exc}")
        if gateway is not None:
            try:
                composite.delete_internet_gateway_and_wait_for_state(
                    gateway.id,
                    wait_for_states=[
                        oci.core.models.InternetGateway.LIFECYCLE_STATE_TERMINATED
                    ],
                )
            except Exception as cleanup_exc:
                cleanup_errors.append(f"删除网关失败：{cleanup_exc}")
        if vcn is not None:
            try:
                composite.delete_vcn_and_wait_for_state(
                    vcn.id,
                    wait_for_states=[oci.core.models.Vcn.LIFECYCLE_STATE_TERMINATED],
                )
            except Exception as cleanup_exc:
                cleanup_errors.append(f"删除 VCN 失败：{cleanup_exc}")
        suffix_text = f"；回滚提示：{'；'.join(cleanup_errors)}" if cleanup_errors else ""
        raise ValueError(
            f"自动创建网络失败（{stage}）：{type(exc).__name__}: {exc}{suffix_text}"
        ) from exc


def load_launch_resources(
    account_id: int,
    *,
    catalog_token: str,
    compartment_id: str,
    availability_domain: str,
    architecture: str,
    network_compartment_id: str | None = None,
    preferred_subnet_id: str | None = None,
) -> dict:
    snapshot = _snapshot_or_error(catalog_token, account_id)
    if compartment_id not in snapshot["compartment_ids"]:
        raise ValueError("所选实例区间不属于当前租户目录")
    if availability_domain not in snapshot["availability_domains"]:
        raise ValueError("所选可用域不属于当前租户目录")

    selected_network_compartment = str(
        network_compartment_id or compartment_id
    ).strip()
    if selected_network_compartment not in snapshot["compartment_ids"]:
        raise ValueError("所选网络区间不属于当前租户目录")

    architecture = normalize_architecture(architecture)
    selected_shape = shape_for_architecture(architecture)

    _, config, proxy_url = get_client_bundle(account_id, snapshot["region"])
    compute = compute_client(config, proxy_url)
    network = network_client(config, proxy_url)

    # Query all accessible compartments only when the user explicitly reads
    # launch resources. The preferred/root compartment is ranked first, while
    # public-capable regional subnets are preferred over private/AD-specific ones.
    subnet_items, subnet_errors = _collect_available_subnets(
        network,
        snapshot["compartment_ids"],
        selected_network_compartment,
    )
    preferred_subnet_id = str(preferred_subnet_id or "").strip()
    if preferred_subnet_id and not any(
        str(item.get("id") or "") == preferred_subnet_id
        for item in subnet_items
    ):
        try:
            preferred = network.get_subnet(
                preferred_subnet_id,
                retry_strategy=_none_retry(),
            ).data
            preferred_compartment = str(
                getattr(preferred, "compartment_id", "") or ""
            )
            preferred_state = str(
                getattr(preferred, "lifecycle_state", "") or ""
            ).upper()
            if preferred_compartment not in snapshot["compartment_ids"]:
                raise ValueError("已选子网不属于当前租户目录")
            if preferred_state and preferred_state != "AVAILABLE":
                raise ValueError(f"已选子网状态为 {preferred_state}")
            subnet_items.insert(0, _subnet_item(preferred, network))
        except Exception as exc:
            subnet_errors.append(
                f"验证已选子网失败：{type(exc).__name__}: {exc}"
            )
    subnet_source_compartments = list(dict.fromkeys(
        str(item.get("compartment_id") or "")
        for item in subnet_items
        if item.get("compartment_id")
    ))
    subnet_fallback_used = bool(
        subnet_items
        and str(subnet_items[0].get("compartment_id") or "")
        != selected_network_compartment
    )
    recommended_subnet = _recommended_public_subnet(subnet_items)

    # Subnet selection must stay usable even when the selected AD does not
    # offer the requested shape or an Ubuntu image. Return partial results and
    # expose a precise error instead of failing the entire resource request.
    matching_shapes: list[Any] = []
    shape_error: str | None = None
    try:
        shapes = oci.pagination.list_call_get_all_results(
            compute.list_shapes,
            compartment_id,
            availability_domain=availability_domain,
            retry_strategy=_none_retry(),
        ).data
        matching_shapes = [
            item for item in shapes
            if str(getattr(item, "shape", "")) == selected_shape
        ]
        if not matching_shapes:
            shape_error = (
                f"当前可用域不支持 {architecture} 配置 {selected_shape}"
            )
    except Exception as exc:
        shape_error = f"读取配置失败：{type(exc).__name__}: {exc}"

    images: list[Any] = []
    image_error: str | None = None
    image_attempt_errors: list[str] = []

    # Image discovery is intentionally independent from matching_shapes.
    # A selected AD can temporarily/reportingly omit A1/E2 from list_shapes;
    # that must not suppress the Ubuntu image list itself.
    if matching_shapes:
        shape_variants = (
            ("Canonical Ubuntu + Shape", {
                "operating_system": "Canonical Ubuntu",
                "shape": selected_shape,
            }),
            ("Ubuntu by Shape", {
                "shape": selected_shape,
            }),
        )
        for image_source, extra_args in shape_variants:
            try:
                query_args = {
                    "lifecycle_state": "AVAILABLE",
                    "sort_by": "TIMECREATED",
                    "sort_order": "DESC",
                    "retry_strategy": _none_retry(),
                }
                query_args.update(extra_args)
                candidates = oci.pagination.list_call_get_all_results(
                    compute.list_images,
                    compartment_id,
                    **query_args,
                ).data
                filtered = [item for item in candidates if _is_ubuntu_image(item)]
                if filtered:
                    images = filtered
                    break
            except Exception as exc:
                image_attempt_errors.append(
                    f"{image_source}: {type(exc).__name__}: {exc}"
                )

    # Real fallback: query Ubuntu without Shape even when the AD's Shape
    # lookup is empty. Then verify target Shape compatibility per image
    # through OCI's Image-Shape Compatibility API.
    if not images:
        fallback_variants = (
            ("Canonical Ubuntu without Shape", {
                "operating_system": "Canonical Ubuntu",
            }),
            ("Ubuntu without Shape", {}),
        )
        for image_source, extra_args in fallback_variants:
            try:
                query_args = {
                    "lifecycle_state": "AVAILABLE",
                    "sort_by": "TIMECREATED",
                    "sort_order": "DESC",
                    "retry_strategy": _none_retry(),
                }
                query_args.update(extra_args)
                candidates = oci.pagination.list_call_get_all_results(
                    compute.list_images,
                    compartment_id,
                    **query_args,
                ).data
                ubuntu_candidates = [
                    item for item in candidates
                    if _is_ubuntu_image(item)
                ][:40]
                compatible: list[Any] = []
                for item in ubuntu_candidates:
                    if _image_supports_shape(
                        compute,
                        str(getattr(item, "id", "") or ""),
                        selected_shape,
                    ):
                        compatible.append(item)
                    if len(compatible) >= 12:
                        break
                if compatible:
                    images = compatible
                    break
            except Exception as exc:
                image_attempt_errors.append(
                    f"{image_source}: {type(exc).__name__}: {exc}"
                )

    if not images:
        image_error = (
            f"未读取到兼容 {architecture} / {selected_shape} 的 Ubuntu 镜像"
        )
        if image_attempt_errors:
            image_error += f"；最后查询错误：{image_attempt_errors[-1][:300]}"
        elif shape_error:
            image_error += f"；配置提示：{shape_error}"

    image_items = [
        {
            "id": item.id,
            "display_name": item.display_name,
            "operating_system": getattr(item, "operating_system", None),
            "operating_system_version": getattr(
                item,
                "operating_system_version",
                None,
            ),
            "size_in_mbs": getattr(item, "size_in_mbs", None),
            "time_created": (
                item.time_created.isoformat()
                if getattr(item, "time_created", None)
                else None
            ),
        }
        for item in images[:100]
    ]
    shape_items: list[dict] = []
    if matching_shapes:
        shape_item = matching_shapes[0]
        shape_items = [
            {
                "architecture": architecture,
                "name": selected_shape,
                "label": f"{architecture} · {selected_shape}",
                "is_flexible": architecture == "ARM",
                "ocpus": getattr(shape_item, "ocpus", None),
                "memory_in_gbs": getattr(shape_item, "memory_in_gbs", None),
                "ocpu_options": _to_dict(
                    getattr(shape_item, "ocpu_options", None)
                ),
                "memory_options": _to_dict(
                    getattr(shape_item, "memory_options", None)
                ),
                "processor_description": getattr(
                    shape_item,
                    "processor_description",
                    None,
                ),
            }
        ]

    subnet_ids = [item["id"] for item in subnet_items]
    # A transient OCI/proxy listing error must not invalidate a subnet that
    # was already verified in this catalog snapshot. Keep the previous IDs
    # only when the live scan reported errors; an error-free empty result is
    # treated as authoritative.
    if not subnet_ids and subnet_errors:
        previous = get_catalog_resources(
            catalog_token,
            compartment_id,
            availability_domain,
        )
        if previous:
            subnet_ids = list(previous.get("subnet_ids") or [])

    save_catalog_resources(
        token=catalog_token,
        compartment_id=compartment_id,
        availability_domain=availability_domain,
        subnet_ids=subnet_ids,
        image_ids=[item["id"] for item in image_items],
        shape_names=[selected_shape] if matching_shapes else [],
    )
    return {
        "catalog_token": catalog_token,
        "account_id": account_id,
        "region": snapshot["region"],
        "compartment_id": compartment_id,
        "network_compartment_id": selected_network_compartment,
        "subnet_source_compartment_ids": subnet_source_compartments,
        "subnet_fallback_used": subnet_fallback_used,
        "subnet_errors": subnet_errors,
        "recommended_subnet_id": (
            recommended_subnet["id"] if recommended_subnet else None
        ),
        "recommended_subnet": recommended_subnet,
        "network_required": not bool(subnet_items),
        "availability_domain": availability_domain,
        "architecture": architecture,
        "shape": selected_shape if matching_shapes else None,
        "shape_error": shape_error,
        "image_error": image_error,
        "subnets": subnet_items,
        "images": image_items,
        "shapes": shape_items,
    }


def validate_launch_request(account_id: int, request: dict, *, allow_expired: bool = False) -> dict:
    token = str(request.get("catalog_token") or "").strip()
    snapshot = _snapshot_or_error(token, account_id, allow_expired=allow_expired)
    compartment_id = str(request.get("compartment_id") or "").strip()
    availability_domain = str(request.get("availability_domain") or "").strip()
    if compartment_id not in snapshot["compartment_ids"]:
        raise ValueError("区间不属于当前租户目录")
    if availability_domain not in snapshot["availability_domains"]:
        raise ValueError("可用域不属于当前租户目录")
    resources = get_catalog_resources(
        token,
        compartment_id,
        availability_domain,
    )
    if not resources:
        raise ValueError("请先读取当前区间的子网、镜像和配置")
    subnet_id = str(request.get("subnet_id") or "").strip()
    image_id = str(request.get("image_id") or "").strip()
    architecture = normalize_architecture(request.get("architecture"))
    shape = shape_for_architecture(architecture)
    if subnet_id not in resources["subnet_ids"]:
        raise ValueError("子网不属于当前租户目录")
    if image_id not in resources["image_ids"]:
        raise ValueError("镜像不是当前租户、架构下已读取的 Ubuntu 镜像")
    if shape not in resources["shape_names"]:
        raise ValueError("配置不属于当前租户目录，或架构已切换，请重新读取资源")
    normalized = dict(request)
    normalized["region"] = snapshot["region"]
    normalized["catalog_token"] = token
    normalized["compartment_id"] = compartment_id
    normalized["availability_domain"] = availability_domain
    normalized["subnet_id"] = subnet_id
    normalized["image_id"] = image_id
    normalized["architecture"] = architecture
    normalized["shape"] = shape
    if architecture == "AMD":
        # VM.Standard.E2.1.Micro is fixed-size; shape_config must not be sent.
        normalized["ocpus"] = None
        normalized["memory_in_gbs"] = None
    return normalized


def display_name_for_sequence(base_name: str, requested_count: int, sequence_no: int) -> str:
    base = str(base_name or "N&T").strip() or "N&T"
    if requested_count <= 1:
        return base
    return f"{base}-{sequence_no}"


def launch_one_instance(
    account_id: int,
    request: dict,
    *,
    sequence_no: int,
    requested_count: int,
    trusted_job_request: bool = False,
) -> dict:
    opc_retry_token = str(request.get("_opc_retry_token") or uuid.uuid4())
    request = validate_launch_request(
        account_id,
        request,
        allow_expired=trusted_job_request,
    )
    _, config, proxy_url = get_client_bundle(account_id, request["region"])
    compute = compute_client(config, proxy_url)

    source_details = oci.core.models.InstanceSourceViaImageDetails(
        source_type="image",
        image_id=request["image_id"],
        boot_volume_size_in_gbs=request.get("boot_volume_size_in_gbs"),
    )
    vnic_details = oci.core.models.CreateVnicDetails(
        subnet_id=request["subnet_id"],
        assign_public_ip=bool(request.get("assign_public_ip", True)),
        assign_ipv6_ip=bool(request.get("assign_ipv6_ip", False)),
    )
    shape_config = None
    if request.get("shape") == SUPPORTED_LAUNCH_SHAPES["ARM"] and (
        request.get("ocpus") is not None or request.get("memory_in_gbs") is not None
    ):
        shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=request.get("ocpus"),
            memory_in_gbs=request.get("memory_in_gbs"),
        )
    metadata = {}
    login_mode = str(
        request.get("login_mode") or "SSH_KEY"
    ).strip().upper()

    if login_mode == "ROOT_PASSWORD":
        encrypted_password = str(
            request.get("root_password_encrypted") or ""
        ).strip()
        if not encrypted_password:
            raise ValueError("root 密码配置不存在")

        try:
            root_password = decrypt_secret(encrypted_password)
        except Exception as exc:
            raise ValueError("root 密码解密失败") from exc

        # JSON 双引号字符串同时是合法 YAML 标量，可避免密码破坏 YAML。
        yaml_password = json.dumps(root_password, ensure_ascii=False)

        cloud_config = f"""#cloud-config
disable_root: false
ssh_pwauth: true
chpasswd:
  expire: false
  users:
    - name: root
      password: {yaml_password}
      type: text
write_files:
  - path: /etc/ssh/sshd_config.d/99-oci-nt-root.conf
    owner: root:root
    permissions: '0600'
    content: |
      PermitRootLogin yes
      PasswordAuthentication yes
      KbdInteractiveAuthentication yes
runcmd:
  - [sh, -c, "systemctl restart ssh || systemctl restart sshd"]
"""
        metadata["user_data"] = base64.b64encode(
            cloud_config.encode("utf-8")
        ).decode("ascii")
    else:
        ssh_key = str(request.get("ssh_public_key") or "").strip()
        if not ssh_key:
            raise ValueError("选择 SSH 密钥登录时必须填写 SSH 公钥")
        metadata["ssh_authorized_keys"] = ssh_key

    display_name = display_name_for_sequence(
        request.get("display_name") or "N&T",
        requested_count,
        sequence_no,
    )
    details = oci.core.models.LaunchInstanceDetails(
        availability_domain=request["availability_domain"],
        compartment_id=request["compartment_id"],
        shape=request["shape"],
        display_name=display_name,
        create_vnic_details=vnic_details,
        source_details=source_details,
        shape_config=shape_config,
        metadata=metadata or None,
    )
    instance = compute.launch_instance(
        details,
        # A caller may reuse one token for bounded retries of the same logical
        # instance request. This prevents an uncertain response from creating
        # a duplicate instance. Fresh requests still get a fresh token.
        opc_retry_token=opc_retry_token,
        retry_strategy=_none_retry(),
    ).data
    return {
        "id": instance.id,
        "display_name": instance.display_name,
        "lifecycle_state": instance.lifecycle_state,
        "availability_domain": instance.availability_domain,
        "compartment_id": instance.compartment_id,
        "shape": instance.shape,
        "region": request["region"],
    }




# OCI-N&T V1.0.4 1.0.4-image-usage-fix2: image discovery independent from AD shape
def _image_supports_shape(
    compute,
    image_id: str,
    shape_name: str,
) -> bool:
    try:
        entries = oci.pagination.list_call_get_all_results(
            compute.list_image_shape_compatibility_entries,
            image_id,
            retry_strategy=_none_retry(),
        ).data
        return any(
            str(getattr(entry, "shape", "") or "") == str(shape_name)
            for entry in entries
        )
    except Exception:
        return False


def resolve_instance_public_ip(
    account_id: int,
    region: str,
    compartment_id: str,
    instance_id: str,
    *,
    attempts: int = 8,
    delay_seconds: float = 1.5,
) -> str | None:
    # Called only after a successful launch. This is a short bounded lookup,
    # not a background OCI polling scheduler.
    import time

    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    network = network_client(config, proxy_url)

    attempts = max(1, min(int(attempts), 12))
    delay_seconds = max(0.2, min(float(delay_seconds), 5.0))

    for attempt in range(attempts):
        try:
            attachments = oci.pagination.list_call_get_all_results(
                compute.list_vnic_attachments,
                compartment_id=compartment_id,
                instance_id=instance_id,
                retry_strategy=_none_retry(),
            ).data
            for attachment in attachments:
                vnic_id = str(getattr(attachment, "vnic_id", "") or "").strip()
                if not vnic_id:
                    continue
                vnic = network.get_vnic(
                    vnic_id,
                    retry_strategy=_none_retry(),
                ).data
                public_ip = str(getattr(vnic, "public_ip", "") or "").strip()
                if public_ip:
                    return public_ip
        except Exception:
            # Public IPv4 can appear a few seconds after LaunchInstance returns.
            # Notification lookup failure must not alter the launch result.
            pass
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    return None


def launch_error(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, oci.exceptions.ServiceError):
        code = str(getattr(exc, "code", "") or "")
        message = str(getattr(exc, "message", "") or str(exc))
        status = int(getattr(exc, "status", 0) or 0)
        retryable = (
            code in {"OutOfHostCapacity", "TooManyRequests", "InternalError"}
            or status in {429, 500, 502, 503, 504}
            or "capacity" in message.lower()
        )
        request_id = getattr(exc, "request_id", None)
        text = f"{status} {code}: {message}".strip()
        if request_id:
            text += f"；Request ID: {request_id}"
        return text, retryable
    if isinstance(exc, oci.exceptions.RequestException):
        return f"OCI 网络请求失败：{exc}", True
    return f"{type(exc).__name__}: {exc}", False
