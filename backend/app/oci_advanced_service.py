import time
import uuid
from typing import Any

import oci

from .oci_service import (
    blockstorage_client,
    compute_client,
    datetime_to_iso,
    get_client_bundle,
)
def _none_retry():
    return oci.retry.NoneRetryStrategy()


def _to_dict(value: Any) -> Any:
    return oci.util.to_dict(value) if value is not None else None












def terminate_instance(
    account_id: int,
    instance_id: str,
    *,
    region: str,
    preserve_boot_volume: bool,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    compute.terminate_instance(
        instance_id,
        preserve_boot_volume=preserve_boot_volume,
        retry_strategy=_none_retry(),
    )
    return {
        "id": instance_id,
        "action": "TERMINATE",
        "preserve_boot_volume": preserve_boot_volume,
    }


def update_instance(
    account_id: int,
    instance_id: str,
    *,
    region: str,
    display_name: str | None = None,
    shape: str | None = None,
    ocpus: float | None = None,
    memory_in_gbs: float | None = None,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    shape_config = None
    if ocpus is not None or memory_in_gbs is not None:
        shape_config = oci.core.models.UpdateInstanceShapeConfigDetails(
            ocpus=ocpus,
            memory_in_gbs=memory_in_gbs,
        )
    details = oci.core.models.UpdateInstanceDetails(
        display_name=display_name,
        shape=shape,
        shape_config=shape_config,
    )
    instance = compute.update_instance(
        instance_id,
        details,
        retry_strategy=_none_retry(),
    ).data
    return {
        "id": instance.id,
        "display_name": instance.display_name,
        "lifecycle_state": instance.lifecycle_state,
        "shape": instance.shape,
        "shape_config": _to_dict(getattr(instance, "shape_config", None)),
    }


def create_console_connection(
    account_id: int,
    instance_id: str,
    *,
    region: str,
    public_key: str,
) -> dict:
    if not public_key.strip().startswith("ssh-"):
        raise ValueError("VNC 公钥必须是 OpenSSH 公钥")
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    details = oci.core.models.CreateInstanceConsoleConnectionDetails(
        instance_id=instance_id,
        public_key=public_key.strip(),
    )
    connection = compute.create_instance_console_connection(
        details,
        opc_retry_token=str(uuid.uuid4()),
        retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
    ).data
    for _ in range(10):
        if getattr(connection, "lifecycle_state", None) in {"ACTIVE", "FAILED"}:
            break
        time.sleep(1)
        connection = compute.get_instance_console_connection(
            connection.id,
            retry_strategy=_none_retry(),
        ).data
    return {
        "id": connection.id,
        "instance_id": connection.instance_id,
        "lifecycle_state": connection.lifecycle_state,
        "connection_string": getattr(connection, "connection_string", None),
        "vnc_connection_string": getattr(connection, "vnc_connection_string", None),
        "service_host_key_fingerprint": getattr(
            connection,
            "service_host_key_fingerprint",
            None,
        ),
        "time_created": datetime_to_iso(getattr(connection, "time_created", None)),
    }


def list_console_connections(
    account_id: int,
    instance_id: str,
    region: str,
    compartment_id: str,
) -> list[dict]:
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    response = oci.pagination.list_call_get_all_results(
        compute.list_instance_console_connections,
        compartment_id,
        instance_id=instance_id,
        retry_strategy=_none_retry(),
    )
    return [_to_dict(item) for item in response.data]


def delete_console_connection(account_id: int, connection_id: str, region: str) -> None:
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute_client(config, proxy_url).delete_instance_console_connection(
        connection_id,
        retry_strategy=_none_retry(),
    )










def update_boot_volume(account_id: int, volume_id: str, request: dict) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, request["region"])
    block = blockstorage_client(config, proxy_url)
    details = oci.core.models.UpdateBootVolumeDetails(
        display_name=request.get("display_name") or None,
        size_in_gbs=request.get("size_in_gbs"),
        vpus_per_gb=request.get("vpus_per_gb"),
        is_auto_tune_enabled=request.get("is_auto_tune_enabled"),
    )
    return _to_dict(
        block.update_boot_volume(volume_id, details, retry_strategy=_none_retry()).data
    )


def create_boot_volume_backup(account_id: int, volume_id: str, request: dict) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, request["region"])
    block = blockstorage_client(config, proxy_url)
    details = oci.core.models.CreateBootVolumeBackupDetails(
        boot_volume_id=volume_id,
        display_name=request.get("display_name") or None,
        type=str(request.get("type") or "INCREMENTAL").upper(),
    )
    return _to_dict(
        block.create_boot_volume_backup(
            details,
            opc_retry_token=str(uuid.uuid4()),
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
        ).data
    )


def list_boot_volume_backups(
    account_id: int,
    volume_id: str,
    *,
    region: str,
    compartment_id: str,
) -> list[dict]:
    _, config, proxy_url = get_client_bundle(account_id, region)
    block = blockstorage_client(config, proxy_url)
    response = oci.pagination.list_call_get_all_results(
        block.list_boot_volume_backups,
        compartment_id,
        boot_volume_id=volume_id,
        sort_by="TIMECREATED",
        sort_order="DESC",
        retry_strategy=_none_retry(),
    )
    return [_to_dict(item) for item in response.data]


def delete_boot_volume_backup(
    account_id: int,
    backup_id: str,
    *,
    region: str,
) -> None:
    _, config, proxy_url = get_client_bundle(account_id, region)
    blockstorage_client(config, proxy_url).delete_boot_volume_backup(
        backup_id,
        retry_strategy=_none_retry(),
    )


def create_instance_image(
    account_id: int,
    instance_id: str,
    *,
    region: str,
    compartment_id: str,
    display_name: str,
) -> dict:
    _, config, proxy_url = get_client_bundle(account_id, region)
    compute = compute_client(config, proxy_url)
    details = oci.core.models.CreateImageDetails(
        compartment_id=compartment_id,
        instance_id=instance_id,
        display_name=display_name.strip(),
    )
    return _to_dict(
        compute.create_image(
            details,
            opc_retry_token=str(uuid.uuid4()),
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
        ).data
    )
























































