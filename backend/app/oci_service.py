from dataclasses import dataclass
from datetime import datetime

import oci
from cryptography.fernet import InvalidToken

from .account_age import earliest_registration_time
from .account_repository import get_account_with_secrets
from .credential_crypto import decrypt_secret
from .proxy_service import apply_proxy_to_oci_client
from .region_names import get_region_display_name
import random
import time


@dataclass(slots=True)
class OciCheckResult:
    status: str
    email: str | None = None
    user_name: str | None = None
    user_lifecycle_state: str | None = None
    user_time_created: str | None = None
    tenancy_name: str | None = None
    home_region_key: str | None = None
    home_region_name: str | None = None
    account_type: str = "UNKNOWN"
    subscription_plan_type: str | None = None
    subscription_upgrade_state: str | None = None
    subscription_time_start: str | None = None
    error: str | None = None


def datetime_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def classify_account_type(plan_type: str | None, upgrade_state: str | None) -> str:
    normalized_plan = plan_type.upper() if isinstance(plan_type, str) else ""
    normalized_upgrade = upgrade_state.upper() if isinstance(upgrade_state, str) else ""
    if normalized_plan == "PAYG" or normalized_upgrade == "UPGRADED":
        return "PERSONAL_UPGRADED"
    if normalized_plan == "FREE_TIER":
        return "PERSONAL_FREE"
    return "UNKNOWN"


def subscription_score(subscription: object) -> int:
    account_type = classify_account_type(
        getattr(subscription, "plan_type", None),
        getattr(subscription, "upgrade_state", None),
    )
    if account_type == "PERSONAL_UPGRADED":
        return 30
    if account_type == "PERSONAL_FREE":
        return 20
    return 10


def build_config(account: dict, private_key_pem: str, passphrase: str | None = None, region: str | None = None) -> dict:
    config = {
        "tenancy": account["tenancy_ocid"],
        "user": account["user_ocid"],
        "fingerprint": account["fingerprint"],
        "region": region or account["region"],
        "key_content": private_key_pem,
    }
    if passphrase:
        config["pass_phrase"] = passphrase
    return config


def decrypt_account_runtime(account_id: int) -> tuple[dict, str, str | None, str | None]:
    account = get_account_with_secrets(account_id)
    if not account:
        raise KeyError("OCI 账户不存在")
    try:
        private_key_pem = decrypt_secret(account["private_key_encrypted"])
        passphrase = decrypt_secret(account["passphrase_encrypted"]) if account.get("passphrase_encrypted") else None
        encrypted_proxy = account.get("profile_proxy_url_encrypted") or account.get("proxy_url_encrypted")
        proxy_url = decrypt_secret(encrypted_proxy) if account.get("proxy_enabled") and encrypted_proxy else None
    except InvalidToken as exc:
        raise RuntimeError("账户凭据无法解密，请检查 CREDENTIAL_ENCRYPTION_KEY") from exc
    return account, private_key_pem, passphrase, proxy_url


def identity_client(config: dict, proxy_url: str | None = None):
    client = oci.identity.IdentityClient(config, timeout=(10, 30))
    apply_proxy_to_oci_client(client, proxy_url)
    return client


def compute_client(config: dict, proxy_url: str | None = None):
    client = oci.core.ComputeClient(config, timeout=(10, 45))
    apply_proxy_to_oci_client(client, proxy_url)
    return client


def network_client(config: dict, proxy_url: str | None = None):
    client = oci.core.VirtualNetworkClient(config, timeout=(10, 45))
    apply_proxy_to_oci_client(client, proxy_url)
    return client


def blockstorage_client(config: dict, proxy_url: str | None = None):
    client = oci.core.BlockstorageClient(config, timeout=(10, 45))
    apply_proxy_to_oci_client(client, proxy_url)
    return client


def read_subscription_metadata(config: dict, identity, tenancy_ocid: str, fallback_home_region_key: str | None, proxy_url: str | None = None) -> dict:
    metadata = {
        "home_region_key": fallback_home_region_key,
        "home_region_name": None,
        "account_type": "UNKNOWN",
        "subscription_plan_type": None,
        "subscription_upgrade_state": None,
        "subscription_time_start": None,
    }
    try:
        region_subscriptions = identity.list_region_subscriptions(
            tenancy_ocid,
            retry_strategy=oci.retry.NoneRetryStrategy(),
        ).data
        home_region = next((item for item in region_subscriptions if getattr(item, "is_home_region", False)), None)
        if home_region:
            metadata["home_region_key"] = getattr(home_region, "region_key", fallback_home_region_key)
            metadata["home_region_name"] = getattr(home_region, "region_name", None)
    except Exception:
        pass

    home_region_name = metadata["home_region_name"] or config.get("region")
    if not home_region_name:
        return metadata

    try:
        osp_config = dict(config)
        osp_config["region"] = home_region_name
        subscription_client = oci.osp_gateway.SubscriptionServiceClient(osp_config, timeout=(10, 30))
        apply_proxy_to_oci_client(subscription_client, proxy_url)
        response = subscription_client.list_subscriptions(
            osp_home_region=home_region_name,
            compartment_id=tenancy_ocid,
            limit=100,
            retry_strategy=oci.retry.NoneRetryStrategy(),
        )
        items = list(getattr(response.data, "items", None) or [])
        if not items:
            return metadata
        subscription = max(items, key=subscription_score)
        plan_type = getattr(subscription, "plan_type", None)
        upgrade_state = getattr(subscription, "upgrade_state", None)
        # Classification follows the strongest/current plan, while registration
        # time uses the earliest subscription start. Upgraded accounts may have
        # a newer PAYG subscription in addition to their original free-tier
        # subscription; using the upgraded start would incorrectly reset age.
        registration_time = earliest_registration_time(
            [getattr(item, "time_start", None) for item in items]
        )
        metadata.update(
            {
                "account_type": classify_account_type(plan_type, upgrade_state),
                "subscription_plan_type": plan_type,
                "subscription_upgrade_state": upgrade_state,
                "subscription_time_start": registration_time,
            }
        )
    except Exception:
        pass
    return metadata




# OCI-N&T V1.0.4 ACCOUNT_CHECK_NETWORK_RETRY_V1
_ACCOUNT_CHECK_NETWORK_RETRY_DELAYS = ((2.0, 4.0), (5.0, 8.0))


def _read_identity_for_account_check(
    config: dict,
    proxy_url: str | None,
    user_ocid: str,
    tenancy_ocid: str,
):
    """Read the two mandatory Identity objects with network-only retries.

    OCI ServiceError responses (401/403/404/etc.) are never retried here.
    A fresh IdentityClient is created for every network retry so a stale SOCKS,
    TCP or TLS session is not reused.
    """
    last_error = None
    for attempt in range(3):
        try:
            identity = identity_client(config, proxy_url)
            user = identity.get_user(
                user_ocid,
                retry_strategy=oci.retry.NoneRetryStrategy(),
            ).data
            tenancy = identity.get_tenancy(
                tenancy_ocid,
                retry_strategy=oci.retry.NoneRetryStrategy(),
            ).data
            return identity, user, tenancy
        except oci.exceptions.RequestException as exc:
            last_error = exc
            if attempt >= 2:
                raise
            low, high = _ACCOUNT_CHECK_NETWORK_RETRY_DELAYS[attempt]
            time.sleep(random.SystemRandom().uniform(low, high))
    if last_error is not None:
        raise last_error
    raise RuntimeError("OCI Identity 检测未返回结果")


def check_oci_credentials(
    *,
    tenancy_ocid: str,
    user_ocid: str,
    fingerprint: str,
    region: str,
    private_key_pem: str,
    private_key_passphrase: str | None,
    proxy_url: str | None = None,
) -> OciCheckResult:
    config = {
        "tenancy": tenancy_ocid,
        "user": user_ocid,
        "fingerprint": fingerprint,
        "region": region,
        "key_content": private_key_pem,
    }
    if private_key_passphrase:
        config["pass_phrase"] = private_key_passphrase
    try:
        oci.config.validate_config(config)
        identity, user, tenancy = _read_identity_for_account_check(
            config,
            proxy_url,
            user_ocid,
            tenancy_ocid,
        )

        email = getattr(user, "email", None)
        if not email:
            possible_email = getattr(user, "name", None)
            if isinstance(possible_email, str) and "@" in possible_email:
                email = possible_email
        lifecycle_state = getattr(user, "lifecycle_state", None)
        status = "ALIVE" if lifecycle_state == "ACTIVE" else "INVALID"
        error = None if status == "ALIVE" else f"OCI 用户状态不是 ACTIVE：{lifecycle_state or 'UNKNOWN'}"
        subscription = read_subscription_metadata(
            config,
            identity,
            tenancy_ocid,
            getattr(tenancy, "home_region_key", None),
            proxy_url,
        )
        return OciCheckResult(
            status=status,
            email=email,
            user_name=getattr(user, "name", None),
            user_lifecycle_state=lifecycle_state,
            user_time_created=datetime_to_iso(getattr(user, "time_created", None)),
            tenancy_name=getattr(tenancy, "name", None),
            home_region_key=subscription["home_region_key"],
            home_region_name=subscription["home_region_name"],
            account_type=subscription["account_type"],
            subscription_plan_type=subscription["subscription_plan_type"],
            subscription_upgrade_state=subscription["subscription_upgrade_state"],
            subscription_time_start=subscription["subscription_time_start"],
            error=error,
        )
    except oci.exceptions.ServiceError as exc:
        if exc.status in (401, 404):
            account_status = "INVALID"
        elif exc.status == 403:
            account_status = "FORBIDDEN"
        else:
            account_status = "ERROR"
        error = f"{exc.status} {exc.code}: {exc.message}"
        request_id = getattr(exc, "request_id", None)
        if request_id:
            error += f"；Request ID: {request_id}"
        return OciCheckResult(status=account_status, error=error)
    except oci.exceptions.RequestException as exc:
        return OciCheckResult(status="ERROR", error=f"OCI 网络请求失败（已进行网络重试）：{exc}")
    except Exception as exc:
        return OciCheckResult(status="INVALID", error=f"OCI 配置或私钥验证失败：{type(exc).__name__}: {exc}")


def get_client_bundle(account_id: int, region: str | None = None):
    account, private_key_pem, passphrase, proxy_url = decrypt_account_runtime(account_id)
    config = build_config(account, private_key_pem, passphrase, region or account.get("home_region_name") or account.get("region"))
    oci.config.validate_config(config)
    return account, config, proxy_url


def list_accessible_compartments(config: dict, proxy_url: str | None, tenancy_ocid: str) -> list[dict]:
    identity = identity_client(config, proxy_url)
    compartments = [{"id": tenancy_ocid, "name": "root", "path": "root"}]
    try:
        response = oci.pagination.list_call_get_all_results(
            identity.list_compartments,
            tenancy_ocid,
            compartment_id_in_subtree=True,
            access_level="ACCESSIBLE",
            retry_strategy=oci.retry.NoneRetryStrategy(),
        )
        for compartment in response.data:
            if getattr(compartment, "lifecycle_state", None) == "ACTIVE":
                compartments.append(
                    {
                        "id": compartment.id,
                        "name": compartment.name,
                        "path": getattr(compartment, "description", None) or compartment.name,
                    }
                )
    except Exception:
        pass
    return compartments


def instance_to_dict(instance, compartment: dict, vnics: list[dict], boot_volumes: list[dict], region: str) -> dict:
    shape_config = getattr(instance, "shape_config", None)
    return {
        "id": instance.id,
        "display_name": instance.display_name,
        "lifecycle_state": instance.lifecycle_state,
        "availability_domain": instance.availability_domain,
        "compartment_id": compartment["id"],
        "compartment_name": compartment["name"],
        "region": region,
        "region_label": get_region_display_name(region),
        "shape": instance.shape,
        "ocpus": getattr(shape_config, "ocpus", None) if shape_config else None,
        "memory_in_gbs": getattr(shape_config, "memory_in_gbs", None) if shape_config else None,
        "time_created": datetime_to_iso(getattr(instance, "time_created", None)),
        "vnics": vnics,
        "boot_volumes": boot_volumes,
    }


def list_instances(account_id: int, region: str | None = None) -> list[dict]:
    account, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    network = network_client(config, proxy_url)
    compartments = list_accessible_compartments(config, proxy_url, account["tenancy_ocid"])
    instances: list[dict] = []
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
                        vnic = network.get_vnic(attachment.vnic_id, retry_strategy=oci.retry.NoneRetryStrategy()).data
                        vnics.append(
                            {
                                "id": vnic.id,
                                "display_name": vnic.display_name,
                                "private_ip": vnic.private_ip,
                                "public_ip": vnic.public_ip,
                                "ipv6_addresses": list(getattr(vnic, "ipv6_addresses", None) or []),
                                "is_primary": getattr(attachment, "is_primary", None),
                            }
                        )
                    except Exception:
                        continue
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
                    item = {"id": attachment.boot_volume_id, "attachment_id": attachment.id, "lifecycle_state": attachment.lifecycle_state}
                    try:
                        volume = blockstorage_client(config, proxy_url).get_boot_volume(attachment.boot_volume_id).data
                        item["display_name"] = volume.display_name
                        item["size_in_gbs"] = volume.size_in_gbs
                    except Exception:
                        pass
                    boot_volumes.append(item)
            except Exception:
                pass
            instances.append(instance_to_dict(instance, compartment, vnics, boot_volumes, config["region"]))
    return instances


def instance_action(account_id: int, instance_id: str, action: str, region: str | None = None) -> dict:
    allowed_actions = {"START", "STOP", "SOFTSTOP", "RESET", "SOFTRESET"}
    normalized = action.upper()
    if normalized not in allowed_actions:
        raise ValueError("仅允许 START、STOP、SOFTSTOP、RESET、SOFTRESET")
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    result = compute.instance_action(instance_id, normalized, retry_strategy=oci.retry.NoneRetryStrategy()).data
    return {
        "id": result.id,
        "display_name": result.display_name,
        "lifecycle_state": result.lifecycle_state,
        "action": normalized,
    }

