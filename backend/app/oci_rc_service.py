from __future__ import annotations

import ipaddress
import json
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, BinaryIO

import oci
import requests

from .oci_service import (
    blockstorage_client,
    compute_client,
    datetime_to_iso,
    get_client_bundle,
    identity_client,
    list_accessible_compartments,
    network_client,
)
from .proxy_service import apply_proxy_to_oci_client



# OCI-N&T V1.0.4 1.0.4-image-usage-fix2: usage UTC day boundary
def _usage_day_boundary_utc(value):
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError("Usage API time must be datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _none_retry():
    return oci.retry.NoneRetryStrategy()


def _all_results(callable_, *args, **kwargs):
    return oci.pagination.list_call_get_all_results(callable_, *args, **kwargs).data


def _to_dict(value: Any) -> Any:
    if value is None:
        return None
    return oci.util.to_dict(value)


def _client(factory, config: dict, proxy_url: str | None, timeout=(10, 60)):
    client = factory(config, timeout=timeout)
    apply_proxy_to_oci_client(client, proxy_url)
    return client


def object_storage_client(config: dict, proxy_url: str | None = None):
    return _client(oci.object_storage.ObjectStorageClient, config, proxy_url)


def monitoring_client(config: dict, proxy_url: str | None = None, timeout=(5, 15)):
    return _client(oci.monitoring.MonitoringClient, config, proxy_url, timeout=timeout)


def usage_client(config: dict, proxy_url: str | None = None):
    return _client(oci.usage_api.UsageapiClient, config, proxy_url, timeout=(10, 90))


def limits_client(config: dict, proxy_url: str | None = None):
    return _client(oci.limits.LimitsClient, config, proxy_url)


def audit_client(config: dict, proxy_url: str | None = None):
    return _client(oci.audit.AuditClient, config, proxy_url, timeout=(10, 90))


def list_compartments(account_id: int, region: str | None = None) -> list[dict]:
    account, config, proxy_url = get_client_bundle(account_id, region)
    return list_accessible_compartments(config, proxy_url, account["tenancy_ocid"])


def get_instance_live(account_id: int, instance_id: str, region: str) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    instance = compute_client(config, proxy_url).get_instance(
        instance_id,
        retry_strategy=_none_retry(),
    ).data
    shape_config = getattr(instance, "shape_config", None)
    return {
        "id": instance.id,
        "display_name": instance.display_name,
        "lifecycle_state": instance.lifecycle_state,
        "availability_domain": instance.availability_domain,
        "fault_domain": getattr(instance, "fault_domain", None),
        "compartment_id": instance.compartment_id,
        "shape": instance.shape,
        "ocpus": getattr(shape_config, "ocpus", None) if shape_config else None,
        "memory_in_gbs": (
            getattr(shape_config, "memory_in_gbs", None) if shape_config else None
        ),
        "freeform_tags": dict(getattr(instance, "freeform_tags", None) or {}),
        "defined_tags": _to_dict(getattr(instance, "defined_tags", None)) or {},
        "time_created": datetime_to_iso(getattr(instance, "time_created", None)),
        "region": config["region"],
    }


def update_instance_details(
    account_id: int,
    instance_id: str,
    *,
    region: str,
    display_name: str | None = None,
    note: str | None = None,
    ocpus: float | None = None,
    memory_in_gbs: float | None = None,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    current = compute.get_instance(instance_id, retry_strategy=_none_retry()).data
    freeform_tags = dict(getattr(current, "freeform_tags", None) or {})
    if note is not None:
        normalized_note = note.strip()
        if normalized_note:
            freeform_tags["OCI-N&T-Note"] = normalized_note
        else:
            freeform_tags.pop("OCI-N&T-Note", None)
    shape_config = None
    if ocpus is not None or memory_in_gbs is not None:
        shape_config = oci.core.models.UpdateInstanceShapeConfigDetails(
            ocpus=ocpus,
            memory_in_gbs=memory_in_gbs,
        )
    details = oci.core.models.UpdateInstanceDetails(
        display_name=display_name.strip() if display_name is not None else None,
        shape_config=shape_config,
        freeform_tags=freeform_tags,
    )
    updated = compute.update_instance(
        instance_id,
        details,
        retry_strategy=_none_retry(),
    ).data
    return get_instance_live(account_id, updated.id, config["region"])


def list_vnic_inventory(
    account_id: int,
    instance_id: str,
    *,
    region: str,
    compartment_id: str,
) -> list[dict]:
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    network = network_client(config, proxy_url)
    attachments = _all_results(
        compute.list_vnic_attachments,
        compartment_id,
        instance_id=instance_id,
        retry_strategy=_none_retry(),
    )
    items: list[dict] = []
    for attachment in attachments:
        vnic = network.get_vnic(attachment.vnic_id, retry_strategy=_none_retry()).data
        private_ips = _all_results(
            network.list_private_ips,
            vnic_id=vnic.id,
            retry_strategy=_none_retry(),
        )
        ipv6s = _all_results(
            network.list_ipv6s,
            vnic_id=vnic.id,
            retry_strategy=_none_retry(),
        )
        public_ips: list[dict] = []
        private_ip_items: list[dict] = []
        for private_ip in private_ips:
            public = None
            try:
                public = network.get_public_ip_by_private_ip_id(
                    oci.core.models.GetPublicIpByPrivateIpIdDetails(
                        private_ip_id=private_ip.id
                    ),
                    retry_strategy=_none_retry(),
                ).data
            except oci.exceptions.ServiceError as exc:
                if exc.status != 404:
                    raise
            private_item = _to_dict(private_ip)
            private_item["is_primary"] = bool(getattr(private_ip, "is_primary", False))
            private_item["public_ip"] = _to_dict(public) if public else None
            private_ip_items.append(private_item)
            if public:
                public_ips.append(_to_dict(public))
        subnet = network.get_subnet(vnic.subnet_id, retry_strategy=_none_retry()).data
        items.append(
            {
                "attachment": _to_dict(attachment),
                "vnic": _to_dict(vnic),
                "is_primary": bool(getattr(attachment, "is_primary", False)),
                "private_ips": private_ip_items,
                "public_ips": public_ips,
                "ipv6s": [_to_dict(item) for item in ipv6s],
                "subnet": _to_dict(subnet),
            }
        )
    items.sort(key=lambda item: (not item["is_primary"], item["vnic"].get("display_name") or ""))
    return items


def create_secondary_vnics(
    account_id: int,
    instance_id: str,
    *,
    region: str,
    compartment_id: str,
    subnet_id: str,
    count: int,
    display_name: str,
    assign_public_ip: bool,
    ipv6_count: int,
    skip_source_dest_check: bool,
) -> list[dict]:
    if count < 1 or count > 8:
        raise ValueError("附属 VNIC 数量必须是 1–8")
    if ipv6_count < 0 or ipv6_count > 8:
        raise ValueError("每个 VNIC 的 IPv6 数量必须是 0–8")
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    network = network_client(config, proxy_url)
    # Reading the subnet with the current tenant credentials is the ownership
    # check. OCI allows a subnet to be placed in a child compartment different
    # from the instance compartment, so comparing compartment IDs here would
    # reject legitimate deployments.
    network.get_subnet(subnet_id, retry_strategy=_none_retry())
    results: list[dict] = []
    base = display_name.strip() or "N&T-VNIC"
    for sequence in range(1, count + 1):
        name = base if count == 1 else f"{base}-{sequence}"
        details = oci.core.models.AttachVnicDetails(
            instance_id=instance_id,
            display_name=name,
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=subnet_id,
                display_name=name,
                assign_public_ip=assign_public_ip,
                assign_ipv6_ip=ipv6_count > 0,
                skip_source_dest_check=skip_source_dest_check,
            ),
        )
        attachment = compute.attach_vnic(
            details,
            opc_retry_token=str(uuid.uuid4()),
            retry_strategy=_none_retry(),
        ).data
        # Wait briefly for VNIC ID, then add additional IPv6 addresses.
        for _ in range(30):
            current = compute.get_vnic_attachment(
                attachment.id,
                retry_strategy=_none_retry(),
            ).data
            if getattr(current, "vnic_id", None):
                attachment = current
                break
            time.sleep(1)
        if getattr(attachment, "vnic_id", None) and ipv6_count > 1:
            for _ in range(ipv6_count - 1):
                network.create_ipv6(
                    oci.core.models.CreateIpv6Details(vnic_id=attachment.vnic_id),
                    opc_retry_token=str(uuid.uuid4()),
                    retry_strategy=_none_retry(),
                )
        results.append(_to_dict(attachment))
    return results


def detach_secondary_vnic(
    account_id: int,
    attachment_id: str,
    *,
    region: str,
) -> None:
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    attachment = compute.get_vnic_attachment(
        attachment_id,
        retry_strategy=_none_retry(),
    ).data
    if bool(getattr(attachment, "is_primary", False)):
        raise ValueError("主 VNIC 不能删除")
    compute.detach_vnic(attachment_id, retry_strategy=_none_retry())


def add_ipv6_addresses(
    account_id: int,
    vnic_id: str,
    *,
    region: str,
    count: int,
) -> list[dict]:
    if count < 1 or count > 8:
        raise ValueError("IPv6 数量必须是 1–8")
    _, config, proxy_url = get_client_bundle(account_id, region)
    network = network_client(config, proxy_url)
    network.get_vnic(vnic_id, retry_strategy=_none_retry())
    results = []
    for _ in range(count):
        created = network.create_ipv6(
            oci.core.models.CreateIpv6Details(vnic_id=vnic_id),
            opc_retry_token=str(uuid.uuid4()),
            retry_strategy=_none_retry(),
        ).data
        results.append(_to_dict(created))
    return results


def delete_ipv6_address(account_id: int, ipv6_id: str, *, region: str) -> None:
    _, config, proxy_url = get_client_bundle(account_id, region)
    network_client(config, proxy_url).delete_ipv6(
        ipv6_id,
        retry_strategy=_none_retry(),
    )


def _public_ip_for_private_ip(network, private_ip_id: str):
    try:
        return network.get_public_ip_by_private_ip_id(
            oci.core.models.GetPublicIpByPrivateIpIdDetails(private_ip_id=private_ip_id),
            retry_strategy=_none_retry(),
        ).data
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            return None
        raise


def rotate_public_ip_to_cidr(
    account_id: int,
    private_ip_id: str,
    *,
    region: str,
    cidr: str,
    max_rotations: int = 20,
) -> dict:
    network_range = ipaddress.ip_network(cidr, strict=False)
    if network_range.version != 4:
        raise ValueError("指定网段必须是 IPv4 CIDR")
    if max_rotations < 1 or max_rotations > 100:
        raise ValueError("最大换 IP 次数必须是 1–100")
    _, config, proxy_url = get_client_bundle(account_id, region)
    network = network_client(config, proxy_url)
    private_ip = network.get_private_ip(private_ip_id, retry_strategy=_none_retry()).data
    if not bool(getattr(private_ip, "is_primary", False)):
        raise ValueError("仅允许主私网 IP 执行指定网段换 IP")
    attempts: list[dict] = []
    for attempt_no in range(1, max_rotations + 1):
        existing = _public_ip_for_private_ip(network, private_ip_id)
        if existing and str(getattr(existing, "lifetime", "")).upper() != "EPHEMERAL":
            raise ValueError("当前为保留公网 IP，系统不会自动删除")
        old_ip = getattr(existing, "ip_address", None) if existing else None
        if existing:
            network.delete_public_ip(existing.id, retry_strategy=_none_retry())
            for _ in range(30):
                try:
                    network.get_public_ip(existing.id, retry_strategy=_none_retry())
                except oci.exceptions.ServiceError as exc:
                    if exc.status == 404:
                        break
                    raise
                time.sleep(1)
        created = network.create_public_ip(
            oci.core.models.CreatePublicIpDetails(
                compartment_id=private_ip.compartment_id,
                lifetime="EPHEMERAL",
                private_ip_id=private_ip_id,
                display_name="OCI-N&T CIDR rotate",
            ),
            opc_retry_token=str(uuid.uuid4()),
            retry_strategy=_none_retry(),
        ).data
        new_ip = getattr(created, "ip_address", None)
        for _ in range(30):
            current = network.get_public_ip(created.id, retry_strategy=_none_retry()).data
            if getattr(current, "ip_address", None):
                created = current
                new_ip = current.ip_address
                break
            time.sleep(1)
        matched = bool(new_ip and ipaddress.ip_address(new_ip) in network_range)
        attempts.append(
            {
                "attempt": attempt_no,
                "old_ip": old_ip,
                "new_ip": new_ip,
                "matched": matched,
                "public_ip_id": created.id,
            }
        )
        if matched:
            return {
                "matched": True,
                "cidr": str(network_range),
                "private_ip_id": private_ip_id,
                "new_ip": new_ip,
                "public_ip_id": created.id,
                "attempts": attempts,
            }
    return {
        "matched": False,
        "cidr": str(network_range),
        "private_ip_id": private_ip_id,
        "new_ip": attempts[-1]["new_ip"] if attempts else None,
        "public_ip_id": attempts[-1]["public_ip_id"] if attempts else None,
        "attempts": attempts,
    }


def check_public_ip_quality(public_ip: str, *, timeout: int = 8) -> dict:
    try:
        address = ipaddress.ip_address(public_ip)
    except ValueError as exc:
        raise ValueError("公网 IP 格式不正确") from exc
    if not address.is_global:
        return {
            "ip": public_ip,
            "score": 0,
            "is_global": False,
            "risk": "INVALID",
            "checks": {"global": False},
            "message": "该地址不是公网 IP",
        }

    # This is deliberately a user-triggered, best-effort check. It never runs
    # in the background and treats external metadata failures as unknown.
    metadata: dict = {}
    errors: list[str] = []
    try:
        response = requests.get(
            f"https://ipwho.is/{public_ip}",
            timeout=timeout,
            headers={"User-Agent": "OCI-N&T/7.0"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("success", True):
            metadata = payload
        else:
            errors.append(str(payload.get("message") or "IP metadata failed"))
    except Exception as exc:
        errors.append(f"metadata: {type(exc).__name__}: {exc}")

    checks = {
        "global": True,
        "hosting": bool(metadata.get("connection", {}).get("type") in {"hosting", "business"}),
        "proxy": bool(metadata.get("security", {}).get("proxy", False)),
        "vpn": bool(metadata.get("security", {}).get("vpn", False)),
        "tor": bool(metadata.get("security", {}).get("tor", False)),
        "crawler": bool(metadata.get("security", {}).get("crawler", False)),
    }
    score = 100
    if checks["hosting"]:
        score -= 10
    if checks["proxy"]:
        score -= 35
    if checks["vpn"]:
        score -= 25
    if checks["tor"]:
        score -= 45
    if checks["crawler"]:
        score -= 20
    if not metadata:
        score = 50
    score = max(0, min(100, score))
    risk = "LOW" if score >= 80 else "MEDIUM" if score >= 60 else "HIGH"
    return {
        "ip": public_ip,
        "score": score,
        "risk": risk,
        "is_global": True,
        "country": metadata.get("country"),
        "region": metadata.get("region"),
        "city": metadata.get("city"),
        "isp": metadata.get("connection", {}).get("isp"),
        "org": metadata.get("connection", {}).get("org"),
        "asn": metadata.get("connection", {}).get("asn"),
        "checks": checks,
        "errors": errors,
        "provider": "ipwho.is",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def get_security_list(account_id: int, security_list_id: str, *, region: str) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    network = network_client(config, proxy_url)
    return _to_dict(
        network.get_security_list(
            security_list_id,
            retry_strategy=_none_retry(),
        ).data
    )


def update_security_list_rules(
    account_id: int,
    security_list_id: str,
    *,
    region: str,
    display_name: str | None,
    ingress_rules: list[dict],
    egress_rules: list[dict],
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    network = network_client(config, proxy_url)

    def port_range(value: dict | None):
        if not value:
            return None
        return oci.core.models.PortRange(min=int(value["min"]), max=int(value["max"]))

    def tcp_options(value: dict | None):
        if not value:
            return None
        return oci.core.models.TcpOptions(
            source_port_range=port_range(value.get("source_port_range")),
            destination_port_range=port_range(value.get("destination_port_range")),
        )

    def udp_options(value: dict | None):
        if not value:
            return None
        return oci.core.models.UdpOptions(
            source_port_range=port_range(value.get("source_port_range")),
            destination_port_range=port_range(value.get("destination_port_range")),
        )

    def icmp_options(value: dict | None):
        if not value:
            return None
        return oci.core.models.IcmpOptions(
            type=int(value["type"]),
            code=int(value["code"]) if value.get("code") is not None else None,
        )

    ingress_models = [
        oci.core.models.IngressSecurityRule(
            protocol=str(item.get("protocol") or "all"),
            source=str(item.get("source") or "0.0.0.0/0"),
            source_type=str(item.get("source_type") or "CIDR_BLOCK"),
            is_stateless=bool(item.get("is_stateless", False)),
            description=item.get("description"),
            tcp_options=tcp_options(item.get("tcp_options")),
            udp_options=udp_options(item.get("udp_options")),
            icmp_options=icmp_options(item.get("icmp_options")),
        )
        for item in ingress_rules
    ]
    egress_models = [
        oci.core.models.EgressSecurityRule(
            protocol=str(item.get("protocol") or "all"),
            destination=str(item.get("destination") or "0.0.0.0/0"),
            destination_type=str(item.get("destination_type") or "CIDR_BLOCK"),
            is_stateless=bool(item.get("is_stateless", False)),
            description=item.get("description"),
            tcp_options=tcp_options(item.get("tcp_options")),
            udp_options=udp_options(item.get("udp_options")),
            icmp_options=icmp_options(item.get("icmp_options")),
        )
        for item in egress_rules
    ]
    details = oci.core.models.UpdateSecurityListDetails(
        display_name=display_name,
        ingress_security_rules=ingress_models,
        egress_security_rules=egress_models,
    )
    return _to_dict(
        network.update_security_list(
            security_list_id,
            details,
            retry_strategy=_none_retry(),
        ).data
    )


def open_all_security_rules(
    account_id: int,
    security_list_id: str,
    *,
    region: str,
) -> dict:
    current = get_security_list(account_id, security_list_id, region=region)
    return update_security_list_rules(
        account_id,
        security_list_id,
        region=region,
        display_name=current.get("display_name"),
        ingress_rules=[
            {
                "protocol": "all",
                "source": "0.0.0.0/0",
                "source_type": "CIDR_BLOCK",
                "is_stateless": False,
                "description": "OCI-N&T open all IPv4",
            },
            {
                "protocol": "all",
                "source": "::/0",
                "source_type": "CIDR_BLOCK",
                "is_stateless": False,
                "description": "OCI-N&T open all IPv6",
            },
        ],
        egress_rules=[
            {
                "protocol": "all",
                "destination": "0.0.0.0/0",
                "destination_type": "CIDR_BLOCK",
                "is_stateless": False,
                "description": "OCI-N&T egress IPv4",
            },
            {
                "protocol": "all",
                "destination": "::/0",
                "destination_type": "CIDR_BLOCK",
                "is_stateless": False,
                "description": "OCI-N&T egress IPv6",
            },
        ],
    )


def list_boot_volumes(
    account_id: int,
    *,
    region: str,
    compartment_id: str,
    availability_domain: str | None = None,
) -> list[dict]:
    _, config, proxy_url = get_client_bundle(account_id, region)
    block = blockstorage_client(config, proxy_url)
    kwargs = {
        "compartment_id": compartment_id,
        "retry_strategy": _none_retry(),
    }
    if availability_domain:
        kwargs["availability_domain"] = availability_domain
    volumes = _all_results(block.list_boot_volumes, **kwargs)
    return [_to_dict(item) for item in volumes]


def delete_boot_volume(
    account_id: int,
    volume_id: str,
    *,
    region: str,
) -> None:
    _, config, proxy_url = get_client_bundle(account_id, region)
    blockstorage_client(config, proxy_url).delete_boot_volume(
        volume_id,
        retry_strategy=_none_retry(),
    )


def list_custom_images(
    account_id: int,
    *,
    region: str,
    compartment_id: str,
) -> list[dict]:
    _, config, proxy_url = get_client_bundle(account_id, region)
    images = _all_results(
        compute_client(config, proxy_url).list_images,
        compartment_id,
        lifecycle_state="AVAILABLE",
        sort_by="TIMECREATED",
        sort_order="DESC",
        retry_strategy=_none_retry(),
    )
    return [
        _to_dict(item)
        for item in images
        if getattr(item, "base_image_id", None) is not None
        or str(getattr(item, "operating_system", "")).lower() == "custom"
    ]


def delete_custom_image(
    account_id: int,
    image_id: str,
    *,
    region: str,
) -> None:
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute_client(config, proxy_url).delete_image(
        image_id,
        retry_strategy=_none_retry(),
    )


def list_region_subscriptions(account_id: int) -> dict:
    account, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    subscriptions = _all_results(
        identity.list_region_subscriptions,
        account["tenancy_ocid"],
        retry_strategy=_none_retry(),
    )
    all_regions = _all_results(identity.list_regions, retry_strategy=_none_retry())
    subscribed_keys = {
        str(getattr(item, "region_key", "")).upper() for item in subscriptions
    }
    subscribed = [_to_dict(item) for item in subscriptions]
    available = [
        _to_dict(item)
        for item in all_regions
        if str(getattr(item, "key", "")).upper() not in subscribed_keys
    ]
    return {"subscribed": subscribed, "available": available}


def subscribe_regions(account_id: int, region_keys: list[str]) -> list[dict]:
    account, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    results: list[dict] = []
    seen: set[str] = set()
    for raw_key in region_keys:
        key = str(raw_key or "").strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        created = identity.create_region_subscription(
            account["tenancy_ocid"],
            oci.identity.models.CreateRegionSubscriptionDetails(region_key=key),
            retry_strategy=_none_retry(),
        ).data
        results.append(_to_dict(created))
    return results


def list_iam_inventory(account_id: int) -> dict:
    """Return OCI IAM inventory without making an invalid unfiltered membership call.

    OCI requires list_user_group_memberships to be filtered by user_id or group_id.
    The previous implementation called the API without either filter, causing the entire
    IAM panel to fail even when users and groups were readable.  This implementation
    returns partial results together with structured errors so one denied sub-operation
    does not hide the rest of the IAM inventory.
    """
    account, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    tenancy = account["tenancy_ocid"]
    errors: list[dict] = []

    try:
        users = _all_results(
            identity.list_users,
            tenancy,
            sort_by="NAME",
            sort_order="ASC",
            retry_strategy=_none_retry(),
        )
    except Exception as exc:
        users = []
        errors.append({
            "scope": "users",
            "message": f"{type(exc).__name__}: {exc}",
        })
        # Even with limited IAM permissions the API key owner may still be readable.
        try:
            users = [
                identity.get_user(
                    config["user"],
                    retry_strategy=_none_retry(),
                ).data
            ]
        except Exception:
            pass

    try:
        groups = _all_results(
            identity.list_groups,
            tenancy,
            sort_by="NAME",
            sort_order="ASC",
            retry_strategy=_none_retry(),
        )
    except Exception as exc:
        groups = []
        errors.append({
            "scope": "groups",
            "message": f"{type(exc).__name__}: {exc}",
        })

    memberships: list = []
    seen_memberships: set[str] = set()
    for user in users:
        user_id = str(getattr(user, "id", "") or "")
        if not user_id:
            continue
        try:
            rows = _all_results(
                identity.list_user_group_memberships,
                tenancy,
                user_id=user_id,
                retry_strategy=_none_retry(),
            )
            for item in rows:
                membership_id = str(getattr(item, "id", "") or "")
                dedupe_key = membership_id or f"{getattr(item, 'user_id', '')}:{getattr(item, 'group_id', '')}"
                if dedupe_key in seen_memberships:
                    continue
                seen_memberships.add(dedupe_key)
                memberships.append(item)
        except Exception as exc:
            errors.append({
                "scope": "memberships",
                "resource_id": user_id,
                "resource_name": str(getattr(user, "name", "") or ""),
                "message": f"{type(exc).__name__}: {exc}",
            })

    return {
        "users": [_to_dict(item) for item in users],
        "groups": [_to_dict(item) for item in groups],
        "memberships": [_to_dict(item) for item in memberships],
        "errors": errors,
    }


def create_iam_user(
    account_id: int,
    *,
    name: str,
    description: str | None,
    email: str | None,
) -> dict:
    account, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    details = oci.identity.models.CreateUserDetails(
        compartment_id=account["tenancy_ocid"],
        name=name.strip(),
        # OCI marks description as required for legacy IAM users.  Empty text is
        # valid, but omitting the field can make the create request fail.
        description=(description or "").strip(),
        email=(email or "").strip() or None,
    )
    return _to_dict(identity.create_user(details, retry_strategy=_none_retry()).data)



def update_iam_user_email(account_id: int, user_id: str, email: str) -> dict:
    """Update the IAM user's notification email through OCI Identity."""
    _, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    details = oci.identity.models.UpdateUserDetails(email=email.strip())
    return _to_dict(
        identity.update_user(
            user_id,
            details,
            retry_strategy=_none_retry(),
        ).data
    )


def reset_iam_mfa(account_id: int, user_id: str) -> dict:
    """Delete all legacy IAM TOTP devices for a user.

    OCI legacy IAM supports at most one TOTP device, but iterating over every
    returned device keeps the operation idempotent and produces a useful audit
    result.  Identity-domain-specific MFA that is not exposed through this
    legacy endpoint is reported by OCI instead of being silently ignored.
    """
    _, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    devices = _all_results(
        identity.list_mfa_totp_devices,
        user_id,
        retry_strategy=_none_retry(),
    )
    deleted: list[dict] = []
    for device in devices:
        device_id = str(getattr(device, "id", "") or "")
        if not device_id:
            continue
        identity.delete_mfa_totp_device(
            user_id,
            device_id,
            retry_strategy=_none_retry(),
        )
        deleted.append({
            "id": device_id,
            "lifecycle_state": getattr(device, "lifecycle_state", None),
            "time_created": getattr(device, "time_created", None),
        })
    return {
        "deleted_count": len(deleted),
        "devices": [_to_dict(item) for item in deleted],
        "mfa_activated": False,
    }

def delete_iam_user(account_id: int, user_id: str) -> None:
    _, config, proxy_url = get_client_bundle(account_id)
    identity_client(config, proxy_url).delete_user(user_id, retry_strategy=_none_retry())


def add_iam_user_to_group(account_id: int, user_id: str, group_id: str) -> dict:
    _, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    details = oci.identity.models.AddUserToGroupDetails(
        user_id=user_id,
        group_id=group_id,
    )
    return _to_dict(
        identity.add_user_to_group(details, retry_strategy=_none_retry()).data
    )


def remove_iam_membership(account_id: int, membership_id: str) -> None:
    _, config, proxy_url = get_client_bundle(account_id)
    identity_client(config, proxy_url).remove_user_from_group(
        membership_id,
        retry_strategy=_none_retry(),
    )


def reset_iam_console_password(account_id: int, user_id: str) -> dict:
    _, config, proxy_url = get_client_bundle(account_id)
    password = identity_client(config, proxy_url).create_or_reset_ui_password(
        user_id,
        retry_strategy=_none_retry(),
    ).data
    return _to_dict(password)


def get_authentication_policy(account_id: int) -> dict:
    account, config, proxy_url = get_client_bundle(account_id)
    policy = identity_client(config, proxy_url).get_authentication_policy(
        account["tenancy_ocid"],
        retry_strategy=_none_retry(),
    ).data
    return _to_dict(policy)


def update_authentication_policy(account_id: int, payload: dict) -> dict:
    account, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    details = oci.identity.models.UpdateAuthenticationPolicyDetails(
        password_policy=oci.identity.models.PasswordPolicy(
            minimum_password_length=payload.get("minimum_password_length"),
            is_uppercase_characters_required=payload.get("is_uppercase_characters_required"),
            is_lowercase_characters_required=payload.get("is_lowercase_characters_required"),
            is_numeric_characters_required=payload.get("is_numeric_characters_required"),
            is_special_characters_required=payload.get("is_special_characters_required"),
            is_username_containment_allowed=payload.get("is_username_containment_allowed"),
            password_expiry_duration=payload.get("password_expiry_duration"),
            password_history_count=payload.get("password_history_count"),
            password_lockout_duration=payload.get("password_lockout_duration"),
            password_lockout_maximum_attempts=payload.get("password_lockout_maximum_attempts"),
        )
    )
    return _to_dict(
        identity.update_authentication_policy(
            account["tenancy_ocid"],
            details,
            retry_strategy=_none_retry(),
        ).data
    )


def list_limits(account_id: int, service_name: str | None = None) -> dict:
    account, config, proxy_url = get_client_bundle(account_id)
    client = limits_client(config, proxy_url)
    services = _all_results(
        client.list_services,
        account["tenancy_ocid"],
        retry_strategy=_none_retry(),
    )
    selected_services = [
        item
        for item in services
        if not service_name or str(getattr(item, "name", "")) == service_name
    ]
    values: list[dict] = []
    for service in selected_services:
        name = str(getattr(service, "name", ""))
        try:
            rows = _all_results(
                client.list_limit_values,
                account["tenancy_ocid"],
                service_name=name,
                retry_strategy=_none_retry(),
            )
            for item in rows:
                row = _to_dict(item)
                # LimitValueSummary does not consistently repeat the service name.
                # Enrich it here so the UI can render a complete one-row-per-limit table.
                row["service_name"] = name
                values.append(row)
        except Exception as exc:
            values.append({"service_name": name, "error": f"{type(exc).__name__}: {exc}"})
    return {"services": [_to_dict(item) for item in services], "values": values}


def list_oci_audit_events(
    account_id: int,
    *,
    region: str,
    compartment_id: str,
    hours: int = 24,
    limit: int = 100,
) -> list[dict]:
    if hours < 1 or hours > 2160:
        raise ValueError("审计时间范围必须是 1–2160 小时")
    _, config, proxy_url = get_client_bundle(account_id, region)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    client = audit_client(config, proxy_url)
    events = _all_results(
        client.list_events,
        compartment_id,
        start,
        end,
        retry_strategy=_none_retry(),
    )
    return [_to_dict(item) for item in events[: max(1, min(limit, 1000))]]


def query_instance_metrics(
    account_id: int,
    *,
    region: str,
    compartment_id: str,
    instance_id: str,
    hours: int = 24,
) -> dict:
    if hours < 1 or hours > 744:
        raise ValueError("指标范围必须是 1–744 小时")
    _, config, proxy_url = get_client_bundle(account_id, region)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    interval = "5m" if hours <= 24 else "1h"
    resolution = "5m" if hours <= 24 else "1h"
    queries = {
        "cpu": f'CpuUtilization[{interval}]{{resourceId = "{instance_id}"}}.mean()',
        "network_in": f'NetworksBytesIn[{interval}]{{resourceId = "{instance_id}"}}.sum()',
        "network_out": f'NetworksBytesOut[{interval}]{{resourceId = "{instance_id}"}}.sum()',
        "memory": f'MemoryUtilization[{interval}]{{resourceId = "{instance_id}"}}.mean()',
    }

    def fetch_metric(key: str, query: str) -> tuple[str, Any]:
        # 每个并行任务使用独立 OCI Client/Session，避免共享 Session 冲突。
        client = monitoring_client(config, proxy_url)
        details = oci.monitoring.models.SummarizeMetricsDataDetails(
            namespace="oci_computeagent",
            query=query,
            start_time=start,
            end_time=end,
            resolution=resolution,
        )
        try:
            response = client.summarize_metrics_data(
                compartment_id,
                details,
                retry_strategy=_none_retry(),
            ).data
            return key, [_to_dict(item) for item in response]
        except Exception as exc:
            return key, {"error": f"{type(exc).__name__}: {exc}"}

    result: dict[str, Any] = {}
    with ThreadPoolExecutor(
        max_workers=min(4, len(queries)),
        thread_name_prefix="oci-metrics",
    ) as executor:
        futures = [
            executor.submit(fetch_metric, key, query)
            for key, query in queries.items()
        ]
        for future in as_completed(futures):
            key, value = future.result()
            result[key] = value

    # 保持固定输出顺序，方便前端和原始 JSON 查看。
    result = {
        key: result.get(key, {"error": "指标查询未返回"})
        for key in queries
    }
    return {
        "instance_id": instance_id,
        "region": region,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "metrics": result,
    }


def query_costs(
    account_id: int,
    *,
    start_date: datetime,
    end_date: datetime,
) -> dict:
    account, config, proxy_url = get_client_bundle(account_id)
    client = usage_client(config, proxy_url)
    details = oci.usage_api.models.RequestSummarizedUsagesDetails(tenant_id=account['tenancy_ocid'], time_usage_started=_usage_day_boundary_utc(start_date), time_usage_ended=_usage_day_boundary_utc(end_date), granularity='DAILY', query_type='COST', group_by=['service', 'region'], is_aggregate_by_time=False)
    response = client.request_summarized_usages(
        details,
        retry_strategy=_none_retry(),
    ).data
    return _to_dict(response)


def object_storage_overview(
    account_id: int,
    *,
    region: str,
    compartment_id: str,
) -> dict:
    account, config, proxy_url = get_client_bundle(account_id, region)
    client = object_storage_client(config, proxy_url)
    namespace = client.get_namespace(
        compartment_id=account["tenancy_ocid"],
        retry_strategy=_none_retry(),
    ).data
    buckets = _all_results(
        client.list_buckets,
        namespace,
        compartment_id,
        retry_strategy=_none_retry(),
    )
    return {"namespace": namespace, "buckets": [_to_dict(item) for item in buckets]}


def create_bucket(
    account_id: int,
    *,
    region: str,
    compartment_id: str,
    namespace: str,
    name: str,
    public_access_type: str,
    storage_tier: str = "Standard",
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    client = object_storage_client(config, proxy_url)
    details = oci.object_storage.models.CreateBucketDetails(
        compartment_id=compartment_id,
        name=name,
        public_access_type=public_access_type,
        storage_tier=storage_tier,
    )
    return _to_dict(client.create_bucket(namespace, details, retry_strategy=_none_retry()).data)


def update_bucket_access(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    public_access_type: str,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    client = object_storage_client(config, proxy_url)
    details = oci.object_storage.models.UpdateBucketDetails(
        public_access_type=public_access_type,
    )
    return _to_dict(
        client.update_bucket(
            namespace,
            bucket_name,
            details,
            retry_strategy=_none_retry(),
        ).data
    )


def delete_bucket(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
) -> None:
    _, config, proxy_url = get_client_bundle(account_id, region)
    object_storage_client(config, proxy_url).delete_bucket(
        namespace,
        bucket_name,
        retry_strategy=_none_retry(),
    )


def list_objects(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    prefix: str | None = None,
    delimiter: str | None = "/",
    start: str | None = None,
    limit: int = 100,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    client = object_storage_client(config, proxy_url)
    response = client.list_objects(
        namespace,
        bucket_name,
        prefix=prefix,
        delimiter=delimiter,
        start=start,
        limit=max(1, min(limit, 1000)),
        fields="name,size,timeCreated,md5,etag,storageTier,archivalState",
        retry_strategy=_none_retry(),
    ).data
    return _to_dict(response)


def put_object(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    object_name: str,
    data: BinaryIO | bytes,
    content_length: int,
    content_type: str | None,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    client = object_storage_client(config, proxy_url)
    body = BytesIO(data) if isinstance(data, bytes) else data
    response = client.put_object(
        namespace,
        bucket_name,
        object_name,
        body,
        content_length=content_length,
        content_type=content_type,
        opc_client_request_id=str(uuid.uuid4()),
        retry_strategy=_none_retry(),
    )
    return {"etag": response.headers.get("etag"), "opc_request_id": response.headers.get("opc-request-id")}


def get_object(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    object_name: str,
) -> tuple[bytes, dict]:
    _, config, proxy_url = get_client_bundle(account_id, region)
    response = object_storage_client(config, proxy_url).get_object(
        namespace,
        bucket_name,
        object_name,
        retry_strategy=_none_retry(),
    )
    return response.data.content, dict(response.headers)


def delete_object(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    object_name: str,
) -> None:
    _, config, proxy_url = get_client_bundle(account_id, region)
    object_storage_client(config, proxy_url).delete_object(
        namespace,
        bucket_name,
        object_name,
        retry_strategy=_none_retry(),
    )


def create_preauthenticated_request(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    object_name: str | None,
    name: str,
    access_type: str,
    expires_at: datetime,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    details = oci.object_storage.models.CreatePreauthenticatedRequestDetails(
        name=name,
        access_type=access_type,
        time_expires=expires_at,
        object_name=object_name or None,
    )
    return _to_dict(
        object_storage_client(config, proxy_url).create_preauthenticated_request(
            namespace,
            bucket_name,
            details,
            retry_strategy=_none_retry(),
        ).data
    )


def create_multipart_upload(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    object_name: str,
    content_type: str | None,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    details = oci.object_storage.models.CreateMultipartUploadDetails(
        object=object_name,
        content_type=content_type,
    )
    return _to_dict(
        object_storage_client(config, proxy_url).create_multipart_upload(
            namespace,
            bucket_name,
            details,
            retry_strategy=_none_retry(),
        ).data
    )


def upload_multipart_part(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    object_name: str,
    upload_id: str,
    part_number: int,
    data: BinaryIO | bytes,
    content_length: int,
) -> str:
    _, config, proxy_url = get_client_bundle(account_id, region)
    body = BytesIO(data) if isinstance(data, bytes) else data
    response = object_storage_client(config, proxy_url).upload_part(
        namespace,
        bucket_name,
        object_name,
        upload_id,
        part_number,
        body,
        content_length=content_length,
        retry_strategy=_none_retry(),
    )
    return str(response.headers.get("etag") or "")


def commit_multipart_upload(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    object_name: str,
    upload_id: str,
    parts: list[dict],
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    commit_parts = [
        oci.object_storage.models.CommitMultipartUploadPartDetails(
            part_num=int(item["part_number"]),
            etag=str(item["etag"]),
        )
        for item in parts
    ]
    details = oci.object_storage.models.CommitMultipartUploadDetails(
        parts_to_commit=commit_parts,
    )
    response = object_storage_client(config, proxy_url).commit_multipart_upload(
        namespace,
        bucket_name,
        object_name,
        upload_id,
        details,
        retry_strategy=_none_retry(),
    )
    return {"etag": response.headers.get("etag"), "opc_request_id": response.headers.get("opc-request-id")}


def abort_multipart_upload(
    account_id: int,
    *,
    region: str,
    namespace: str,
    bucket_name: str,
    object_name: str,
    upload_id: str,
) -> None:
    _, config, proxy_url = get_client_bundle(account_id, region)
    object_storage_client(config, proxy_url).abort_multipart_upload(
        namespace,
        bucket_name,
        object_name,
        upload_id,
        retry_strategy=_none_retry(),
    )


def update_vnic_settings(
    account_id: int,
    vnic_id: str,
    *,
    region: str,
    display_name: str | None = None,
    hostname_label: str | None = None,
    skip_source_dest_check: bool | None = None,
    nsg_ids: list[str] | None = None,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    network = network_client(config, proxy_url)
    details = oci.core.models.UpdateVnicDetails(
        display_name=display_name,
        hostname_label=hostname_label,
        skip_source_dest_check=skip_source_dest_check,
        nsg_ids=nsg_ids,
    )
    return _to_dict(
        network.update_vnic(vnic_id, details, retry_strategy=_none_retry()).data
    )


def recover_instance_network(
    account_id: int,
    instance_id: str,
    *,
    region: str,
    compartment_id: str,
) -> dict:
    inventory = list_vnic_inventory(
        account_id,
        instance_id,
        region=region,
        compartment_id=compartment_id,
    )
    if not inventory:
        raise ValueError("实例没有可用 VNIC")
    updated: list[dict] = []
    for item in inventory:
        vnic_id = item["vnic"]["id"]
        # Restore normal host networking. Secondary VNICs that are intentionally
        # used as routers can be switched back to forwarding from their own row.
        updated.append(
            update_vnic_settings(
                account_id,
                vnic_id,
                region=region,
                skip_source_dest_check=False,
            )
        )
    return {"instance_id": instance_id, "vnics": updated, "mode": "NORMAL"}


def configure_vnic_forwarding(
    account_id: int,
    vnic_ids: list[str],
    *,
    region: str,
    enabled: bool,
) -> list[dict]:
    if not vnic_ids:
        raise ValueError("请至少选择一个 VNIC")
    if len(vnic_ids) > 16:
        raise ValueError("一次最多配置 16 个 VNIC")
    return [
        update_vnic_settings(
            account_id,
            vnic_id,
            region=region,
            skip_source_dest_check=bool(enabled),
        )
        for vnic_id in vnic_ids
    ]

# OCI-N&T V1.0.4 1.0.4-cost-dashboard-v2.1: resource daily cost dashboard

# OCI-N&T V1.0.4 1.0.4-cost-dashboard-v2.2: Usage groupBy max4

# OCI-N&T V1.0.4 1.0.4-cost-stabilize-account1: account-wide usage service+region
