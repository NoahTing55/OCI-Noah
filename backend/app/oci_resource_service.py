import re
import time
import uuid

import oci

from .oci_service import (
    blockstorage_client,
    compute_client,
    datetime_to_iso,
    get_client_bundle,
    identity_client,
    list_accessible_compartments,
    network_client,
)
from .region_names import get_region_display_name


_HOSTNAME_LABEL_PATTERN = re.compile(
    r"^[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


def list_ready_region_names(account_id: int) -> list[str]:
    """Return subscribed READY regions for internal instance discovery."""
    account, config, proxy_url = get_client_bundle(account_id)
    identity = identity_client(config, proxy_url)
    subscriptions = identity.list_region_subscriptions(
        account["tenancy_ocid"],
        retry_strategy=oci.retry.NoneRetryStrategy(),
    ).data
    names = {
        str(getattr(item, "region_name", "") or "").strip().lower()
        for item in subscriptions
        if str(getattr(item, "status", "") or "").upper() == "READY"
        and getattr(item, "region_name", None)
    }
    return sorted(names)


def _port_range(value: object | None) -> dict | None:
    if value is None:
        return None
    return {
        "min": getattr(value, "min", None),
        "max": getattr(value, "max", None),
    }


def _rule_options(rule: object) -> dict:
    tcp = getattr(rule, "tcp_options", None)
    udp = getattr(rule, "udp_options", None)
    icmp = getattr(rule, "icmp_options", None)
    options: dict = {}

    if tcp:
        options["tcp"] = {
            "source_port_range": _port_range(getattr(tcp, "source_port_range", None)),
            "destination_port_range": _port_range(
                getattr(tcp, "destination_port_range", None)
            ),
        }
    if udp:
        options["udp"] = {
            "source_port_range": _port_range(getattr(udp, "source_port_range", None)),
            "destination_port_range": _port_range(
                getattr(udp, "destination_port_range", None)
            ),
        }
    if icmp:
        options["icmp"] = {
            "type": getattr(icmp, "type", None),
            "code": getattr(icmp, "code", None),
        }

    return options


def _security_rule_to_dict(rule: object, direction: str) -> dict:
    remote_field = "source" if direction == "INGRESS" else "destination"
    remote_type_field = (
        "source_type" if direction == "INGRESS" else "destination_type"
    )
    return {
        "direction": direction,
        "protocol": getattr(rule, "protocol", None),
        "is_stateless": bool(getattr(rule, "is_stateless", False)),
        "remote": getattr(rule, remote_field, None),
        "remote_type": getattr(rule, remote_type_field, None),
        "description": getattr(rule, "description", None),
        "options": _rule_options(rule),
    }


def _subnet_details(
    network,
    subnet_id: str | None,
    cache: dict[str, dict] | None = None,
) -> dict:
    if not subnet_id:
        return {
            "id": None,
            "display_name": None,
            "cidr_block": None,
            "security_lists": [],
        }

    if cache is not None and subnet_id in cache:
        return cache[subnet_id]

    subnet = network.get_subnet(
        subnet_id,
        retry_strategy=oci.retry.NoneRetryStrategy(),
    ).data
    security_lists: list[dict] = []

    for security_list_id in list(getattr(subnet, "security_list_ids", None) or []):
        try:
            security_list = network.get_security_list(
                security_list_id,
                retry_strategy=oci.retry.NoneRetryStrategy(),
            ).data
        except Exception:
            continue

        ingress = [
            _security_rule_to_dict(rule, "INGRESS")
            for rule in list(getattr(security_list, "ingress_security_rules", None) or [])
        ]
        egress = [
            _security_rule_to_dict(rule, "EGRESS")
            for rule in list(getattr(security_list, "egress_security_rules", None) or [])
        ]
        security_lists.append(
            {
                "id": security_list.id,
                "display_name": security_list.display_name,
                "ingress_rules": ingress,
                "egress_rules": egress,
            }
        )

    result = {
        "id": subnet.id,
        "display_name": subnet.display_name,
        "cidr_block": getattr(subnet, "cidr_block", None),
        "security_lists": security_lists,
    }
    if cache is not None:
        cache[subnet_id] = result
    return result


def _public_ip_for_private_ip(network, private_ip_id: str | None):
    if not private_ip_id:
        return None
    details = oci.core.models.GetPublicIpByPrivateIpIdDetails(
        private_ip_id=private_ip_id,
    )
    try:
        return network.get_public_ip_by_private_ip_id(
            details,
            retry_strategy=oci.retry.NoneRetryStrategy(),
        ).data
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            return None
        raise


def _vnic_to_dict(
    network,
    attachment: object,
    subnet_cache: dict[str, dict] | None = None,
) -> dict:
    vnic = network.get_vnic(
        attachment.vnic_id,
        retry_strategy=oci.retry.NoneRetryStrategy(),
    ).data
    private_ips = network.list_private_ips(
        vnic_id=vnic.id,
        retry_strategy=oci.retry.NoneRetryStrategy(),
    ).data
    primary_private_ip = next(
        (
            item
            for item in private_ips
            if getattr(item, "is_primary", False)
            or getattr(item, "ip_address", None) == getattr(vnic, "private_ip", None)
        ),
        private_ips[0] if private_ips else None,
    )
    private_ip_id = getattr(primary_private_ip, "id", None)
    public_ip = _public_ip_for_private_ip(network, private_ip_id)
    try:
        ipv6_items = network.list_ipv6s(
            vnic_id=vnic.id,
            retry_strategy=oci.retry.NoneRetryStrategy(),
        ).data
    except Exception:
        ipv6_items = []

    try:
        subnet = _subnet_details(
            network,
            getattr(vnic, "subnet_id", None),
            subnet_cache,
        )
    except Exception:
        subnet = {
            "id": getattr(vnic, "subnet_id", None),
            "display_name": None,
            "cidr_block": None,
            "security_lists": [],
        }

    return {
        "id": vnic.id,
        "attachment_id": getattr(attachment, "id", None),
        "display_name": vnic.display_name,
        "private_ip": getattr(vnic, "private_ip", None),
        "private_ip_id": private_ip_id,
        "public_ip": getattr(public_ip, "ip_address", None)
        or getattr(vnic, "public_ip", None),
        "public_ip_id": getattr(public_ip, "id", None),
        "public_ip_lifetime": getattr(public_ip, "lifetime", None),
        "public_ip_state": getattr(public_ip, "lifecycle_state", None),
        "ipv6_addresses": [
            getattr(item, "ip_address", None)
            for item in ipv6_items
            if getattr(item, "ip_address", None)
        ] or list(getattr(vnic, "ipv6_addresses", None) or []),
        "ipv6s": [
            {
                "id": getattr(item, "id", None),
                "ip_address": getattr(item, "ip_address", None),
                "display_name": getattr(item, "display_name", None),
                "lifecycle_state": getattr(item, "lifecycle_state", None),
            }
            for item in ipv6_items
        ],
        "is_primary": bool(
            getattr(vnic, "is_primary", False)
            or getattr(attachment, "is_primary", False)
        ),
        "hostname_label": getattr(vnic, "hostname_label", None),
        "skip_source_dest_check": bool(
            getattr(vnic, "skip_source_dest_check", False)
        ),
        "mac_address": getattr(vnic, "mac_address", None),
        "nsg_ids": list(getattr(vnic, "nsg_ids", None) or []),
        "subnet": subnet,
    }


def _instance_to_dict(
    instance: object,
    compartment: dict,
    vnics: list[dict],
    boot_volumes: list[dict],
    region: str,
) -> dict:
    shape_config = getattr(instance, "shape_config", None)
    return {
        "id": instance.id,
        "display_name": instance.display_name,
        "lifecycle_state": instance.lifecycle_state,
        "availability_domain": instance.availability_domain,
        "fault_domain": getattr(instance, "fault_domain", None),
        "compartment_id": compartment["id"],
        "compartment_name": compartment["name"],
        "region": region,
        "region_label": get_region_display_name(region),
        "shape": instance.shape,
        "ocpus": getattr(shape_config, "ocpus", None) if shape_config else None,
        "memory_in_gbs": (
            getattr(shape_config, "memory_in_gbs", None) if shape_config else None
        ),
        "time_created": datetime_to_iso(getattr(instance, "time_created", None)),
        "vnics": vnics,
        "boot_volumes": boot_volumes,
    }


def list_instances_detailed(account_id: int, region: str | None = None) -> list[dict]:
    account, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    network = network_client(config, proxy_url)
    blockstorage = blockstorage_client(config, proxy_url)
    compartments = list_accessible_compartments(
        config,
        proxy_url,
        account["tenancy_ocid"],
    )
    instances: list[dict] = []
    subnet_cache: dict[str, dict] = {}

    for compartment in compartments:
        try:
            response = oci.pagination.list_call_get_all_results(
                compute.list_instances,
                compartment["id"],
                retry_strategy=oci.retry.NoneRetryStrategy(),
            )
        except Exception:
            continue

        for instance in response.data:
            vnics: list[dict] = []
            try:
                attachments = oci.pagination.list_call_get_all_results(
                    compute.list_vnic_attachments,
                    compartment["id"],
                    instance_id=instance.id,
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                ).data
                for attachment in attachments:
                    try:
                        vnics.append(
                            _vnic_to_dict(network, attachment, subnet_cache)
                        )
                    except Exception as exc:
                        vnics.append(
                            {
                                "id": getattr(attachment, "vnic_id", None),
                                "attachment_id": getattr(attachment, "id", None),
                                "display_name": None,
                                "private_ip": None,
                                "private_ip_id": None,
                                "public_ip": None,
                                "public_ip_id": None,
                                "public_ip_lifetime": None,
                                "public_ip_state": None,
                                "ipv6_addresses": [],
                                "ipv6s": [],
                                "is_primary": bool(
                                    getattr(attachment, "is_primary", False)
                                ),
                                "hostname_label": None,
                                "mac_address": None,
                                "nsg_ids": [],
                                "subnet": None,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
            except Exception:
                pass

            boot_volumes: list[dict] = []
            try:
                attachments = compute.list_boot_volume_attachments(
                    instance.availability_domain,
                    compartment["id"],
                    instance_id=instance.id,
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                ).data
                for attachment in attachments:
                    item = {
                        "id": attachment.boot_volume_id,
                        "attachment_id": attachment.id,
                        "lifecycle_state": attachment.lifecycle_state,
                    }
                    try:
                        volume = blockstorage.get_boot_volume(
                            attachment.boot_volume_id,
                            retry_strategy=oci.retry.NoneRetryStrategy(),
                        ).data
                        item["display_name"] = volume.display_name
                        item["size_in_gbs"] = volume.size_in_gbs
                        item["vpus_per_gb"] = getattr(volume, "vpus_per_gb", None)
                    except Exception:
                        pass
                    boot_volumes.append(item)
            except Exception:
                pass

            instances.append(
                _instance_to_dict(
                    instance,
                    compartment,
                    vnics,
                    boot_volumes,
                    config["region"],
                )
            )

    return instances


def list_instances_all_regions(account_id: int) -> dict:
    """Read instances from every ready region without hiding regional failures."""
    regions = list_ready_region_names(account_id)
    instances: list[dict] = []
    errors: list[dict] = []

    for region in regions:
        try:
            instances.extend(list_instances_detailed(account_id, region))
        except Exception as exc:
            errors.append(
                {
                    "account_id": account_id,
                    "region": region,
                    "region_label": get_region_display_name(region),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    instances.sort(
        key=lambda item: (
            str(item.get("region") or ""),
            str(item.get("display_name") or ""),
        )
    )
    return {
        "instances": instances,
        "errors": errors,
        "regions_scanned": len(regions),
    }




def replace_ephemeral_public_ip(
    account_id: int,
    private_ip_id: str,
    region: str | None = None,
) -> dict:
    normalized_private_ip_id = str(private_ip_id or "").strip()
    if not normalized_private_ip_id.startswith("ocid1.privateip."):
        raise ValueError("Private IP OCID 格式不正确")

    _, config, proxy_url = get_client_bundle(account_id, region)
    network = network_client(config, proxy_url)
    private_ip = network.get_private_ip(
        normalized_private_ip_id,
        retry_strategy=oci.retry.NoneRetryStrategy(),
    ).data
    if not getattr(private_ip, "is_primary", False):
        raise ValueError("只能为主私网 IP 更换临时公网 IP")
    existing = _public_ip_for_private_ip(network, normalized_private_ip_id)
    old_ip = getattr(existing, "ip_address", None) if existing else None

    if existing and str(getattr(existing, "lifetime", "")).upper() != "EPHEMERAL":
        raise ValueError("当前公网 IP 是保留 IP，系统不会自动删除")

    if existing:
        network.delete_public_ip(
            existing.id,
            retry_strategy=oci.retry.NoneRetryStrategy(),
        )
        for _ in range(30):
            try:
                network.get_public_ip(
                    existing.id,
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                )
            except oci.exceptions.ServiceError as exc:
                if exc.status == 404:
                    break
                raise
            time.sleep(1)
        else:
            raise RuntimeError("旧临时公网 IP 删除超时，请稍后重试")

    details = oci.core.models.CreatePublicIpDetails(
        compartment_id=private_ip.compartment_id,
        lifetime="EPHEMERAL",
        private_ip_id=normalized_private_ip_id,
        display_name="OCI-N&T ephemeral public IP",
    )
    created = None
    last_error: Exception | None = None

    for attempt in range(5):
        try:
            created = network.create_public_ip(
                details,
                opc_retry_token=str(uuid.uuid4()),
                retry_strategy=oci.retry.NoneRetryStrategy(),
            ).data
            break
        except oci.exceptions.ServiceError as exc:
            last_error = exc
            if exc.status not in (409, 429) or attempt == 4:
                raise
            time.sleep(2 + attempt)

    if created is None:
        raise RuntimeError(f"新临时公网 IP 创建失败：{last_error}")

    for _ in range(30):
        current = network.get_public_ip(
            created.id,
            retry_strategy=oci.retry.NoneRetryStrategy(),
        ).data
        if getattr(current, "ip_address", None) and getattr(
            current, "lifecycle_state", None
        ) == "ASSIGNED":
            created = current
            break
        time.sleep(1)

    return {
        "private_ip_id": normalized_private_ip_id,
        "old_ip": old_ip,
        "new_ip": getattr(created, "ip_address", None),
        "public_ip_id": created.id,
        "lifetime": getattr(created, "lifetime", "EPHEMERAL"),
        "lifecycle_state": getattr(created, "lifecycle_state", None),
        "region": config["region"],
    }
