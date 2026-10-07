from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from typing import Literal

from .database import add_audit_log, get_user_by_username
from .launch_repository import get_launch_job, list_launch_jobs, reset_launch_job_counters
from .launch_task_service import LaunchBusyError, cancel_launch_task, start_launch_task
from .launch_profile_service import (
    launch_profile_template,
    preflight_launch_payload,
    require_preflight_launch_payload,
)
from .oci_advanced_service import update_boot_volume
from .oci_rc_service import (
    abort_multipart_upload,
    add_iam_user_to_group,
    add_ipv6_addresses,
    check_public_ip_quality,
    commit_multipart_upload,
    configure_vnic_forwarding,
    create_bucket,
    create_iam_user,
    create_multipart_upload,
    create_preauthenticated_request,
    create_secondary_vnics,
    delete_boot_volume,
    delete_bucket,
    delete_custom_image,
    delete_iam_user,
    delete_ipv6_address,
    delete_object,
    detach_secondary_vnic,
    get_authentication_policy,
    get_instance_live,
    get_object,
    get_security_list,
    list_boot_volumes,
    list_compartments,
    list_custom_images,
    list_iam_inventory,
    list_limits,
    list_objects,
    list_oci_audit_events,
    list_region_subscriptions,
    list_vnic_inventory,
    object_storage_overview,
    open_all_security_rules,
    put_object,
    query_costs,
    query_instance_metrics,
    recover_instance_network,
    remove_iam_membership,
    reset_iam_console_password,
    reset_iam_mfa,
    rotate_public_ip_to_cidr,
    subscribe_regions,
    update_authentication_policy,
    update_bucket_access,
    update_iam_user_email,
    update_instance_details,
    update_security_list_rules,
    update_vnic_settings,
    upload_multipart_part,
)
from .rc_repository import (
    add_ip_quality_history,
    create_launch_profile,
    create_multipart_session,
    delete_launch_profile,
    finish_multipart_session,
    get_launch_profile,
    get_multipart_session,
    get_resource_cache,
    list_ip_quality_history,
    list_launch_profiles,
    list_multipart_sessions,
    list_resource_cache,
    list_vnc_sessions,
    replace_resource_type_cache,
    save_multipart_part,
    save_resource_snapshot,
    update_launch_profile,
    set_launch_profiles_enabled,
)
from .auth_session_service import validate_access_token
from .tenant_scope import require_account, require_boot_volume, require_instance_region
from .vnc_runtime import (
    resolve_vnc_target,
    start_vnc_session,
    stop_vnc_session,
)


router = APIRouter(prefix="/api/v1", tags=["OCI-N&T resources"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def current_user(token: str = Depends(oauth2_scheme)) -> dict:
    validated = validate_access_token(token)
    if not validated:
        raise HTTPException(status_code=401, detail="登录状态无效、已注销或已经过期")
    user, session = validated
    result = dict(user)
    result["_session_id"] = session.get("session_id") if session else None
    return result


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def audit(
    user: dict,
    request: Request,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: str | None = None,
) -> None:
    add_audit_log(
        user["username"],
        action,
        client_ip(request),
        detail,
        resource_type,
        resource_id,
    )


def service_error(exc: Exception, prefix: str) -> HTTPException:
    if isinstance(exc, (ValueError, KeyError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(
        status_code=400,
        detail=f"{prefix}：{type(exc).__name__}: {exc}",
    )


def require_prefix(value: str, prefix: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized.startswith(prefix):
        raise HTTPException(status_code=422, detail=f"{label} OCID 格式不正确")
    return normalized


class InstanceUpdateRequest(BaseModel):
    region: str = Field(min_length=3, max_length=64)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=255)
    ocpus: float | None = Field(default=None, gt=0, le=160)
    memory_in_gbs: float | None = Field(default=None, gt=0, le=2048)


class NetworkInventoryRequest(BaseModel):
    region: str = Field(min_length=3, max_length=64)
    compartment_id: str = Field(min_length=20, max_length=255)


class SecondaryVnicCreateRequest(NetworkInventoryRequest):
    subnet_id: str = Field(min_length=20, max_length=255)
    count: int = Field(default=1, ge=1, le=8)
    display_name: str = Field(default="N&T-VNIC", min_length=1, max_length=255)
    assign_public_ip: bool = False
    ipv6_count: int = Field(default=0, ge=0, le=8)
    skip_source_dest_check: bool = False


class RegionRequest(BaseModel):
    region: str = Field(min_length=3, max_length=64)


class Ipv6CreateRequest(RegionRequest):
    count: int = Field(default=1, ge=1, le=8)


class CidrRotateRequest(RegionRequest):
    instance_id: str = Field(min_length=20, max_length=255)
    cidr: str = Field(min_length=7, max_length=64)
    max_rotations: int = Field(default=20, ge=1, le=100)


class IpQualityRequest(RegionRequest):
    instance_id: str = Field(min_length=20, max_length=255)
    private_ip_id: str = Field(min_length=20, max_length=255)
    public_ip: str = Field(min_length=7, max_length=64)


class QualityRotateRequest(CidrRotateRequest):
    minimum_score: int = Field(default=80, ge=0, le=100)


class VnicUpdateRequest(RegionRequest):
    display_name: str | None = Field(default=None, max_length=255)
    hostname_label: str | None = Field(default=None, max_length=63)
    skip_source_dest_check: bool | None = None
    nsg_ids: list[str] | None = Field(default=None, max_length=5)


class ForwardingRequest(RegionRequest):
    vnic_ids: list[str] = Field(min_length=1, max_length=16)
    enabled: bool = True


class BulkVnicDeleteRequest(RegionRequest):
    attachment_ids: list[str] = Field(min_length=1, max_length=16)
    confirmation: Literal["DELETE ALL VNICS"]


class SecurityListUpdateRequest(RegionRequest):
    display_name: str | None = Field(default=None, max_length=255)
    ingress_rules: list[dict] = Field(default_factory=list, max_length=200)
    egress_rules: list[dict] = Field(default_factory=list, max_length=200)


class BootVolumeUpdateRequest(BaseModel):
    region: str = Field(min_length=3, max_length=64)
    display_name: str | None = Field(default=None, max_length=255)
    size_in_gbs: int | None = Field(default=None, ge=50, le=32768)
    vpus_per_gb: int | None = Field(default=None, ge=0, le=120)
    is_auto_tune_enabled: bool | None = None


class RegionSubscribeRequest(BaseModel):
    region_keys: list[str] = Field(min_length=1, max_length=50)


class IamUserCreateRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9._+@-]+$",
    )
    description: str | None = Field(default="", max_length=400)
    email: str | None = Field(
        default=None,
        max_length=255,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    )
    group_id: str | None = Field(default=None, min_length=20, max_length=255)
    create_console_password: bool = True


class IamUserEmailUpdateRequest(BaseModel):
    email: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    )


class IamMfaResetRequest(BaseModel):
    confirmation: Literal["RESET MFA"]


class MembershipRequest(BaseModel):
    user_id: str = Field(min_length=20, max_length=255)
    group_id: str = Field(min_length=20, max_length=255)


class PasswordPolicyRequest(BaseModel):
    minimum_password_length: int | None = Field(default=None, ge=1, le=100)
    is_uppercase_characters_required: bool | None = None
    is_lowercase_characters_required: bool | None = None
    is_numeric_characters_required: bool | None = None
    is_special_characters_required: bool | None = None
    is_username_containment_allowed: bool | None = None
    password_expiry_duration: int | None = Field(default=None, ge=0, le=99999)
    password_history_count: int | None = Field(default=None, ge=0, le=24)
    password_lockout_duration: int | None = Field(default=None, ge=0, le=99999)
    password_lockout_maximum_attempts: int | None = Field(default=None, ge=0, le=100)


class CostQueryRequest(BaseModel):
    start_date: datetime
    end_date: datetime


class BucketCreateRequest(BaseModel):
    region: str = Field(min_length=3, max_length=64)
    compartment_id: str = Field(min_length=20, max_length=255)
    namespace: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=256)
    public_access_type: Literal["NoPublicAccess", "ObjectRead", "ObjectReadWithoutList"] = "NoPublicAccess"
    storage_tier: Literal["Standard", "Archive"] = "Standard"


class BucketAccessRequest(RegionRequest):
    namespace: str = Field(min_length=1, max_length=255)
    public_access_type: Literal["NoPublicAccess", "ObjectRead", "ObjectReadWithoutList"]


class PreauthRequest(BaseModel):
    region: str = Field(min_length=3, max_length=64)
    namespace: str = Field(min_length=1, max_length=255)
    object_name: str | None = Field(default=None, max_length=1024)
    name: str = Field(min_length=1, max_length=256)
    access_type: Literal[
        "AnyObjectRead",
        "AnyObjectWrite",
        "AnyObjectReadWrite",
        "ObjectRead",
        "ObjectWrite",
        "ObjectReadWrite",
    ]
    expires_at: datetime


class MultipartCreateRequest(BaseModel):
    region: str = Field(min_length=3, max_length=64)
    namespace: str = Field(min_length=1, max_length=255)
    bucket_name: str = Field(min_length=1, max_length=256)
    object_name: str = Field(min_length=1, max_length=1024)
    content_type: str | None = Field(default=None, max_length=255)


class LaunchProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    payload: dict
    enabled: bool = True


class LaunchProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    payload: dict | None = None
    enabled: bool | None = None


class VncStartRequest(RegionRequest):
    duration_minutes: int = Field(default=30, ge=5, le=120)


class LaunchProfilesBulkRequest(BaseModel):
    enabled: bool


class LaunchProfileCopyRequest(BaseModel):
    target_account_ids: list[int] = Field(min_length=1, max_length=100)
    enabled: bool = True


class LaunchProfilePreflightRequest(BaseModel):
    apply: bool = True


@router.get("/accounts/{account_id}/compartments")
def compartments(
    account_id: int,
    region: str | None = None,
    _: dict = Depends(current_user),
) -> list[dict]:
    require_account(account_id)
    try:
        return list_compartments(account_id, region)
    except Exception as exc:
        raise service_error(exc, "读取区间失败") from exc


@router.get("/accounts/{account_id}/instances/{instance_id}/live")
def instance_live(
    account_id: int,
    instance_id: str,
    region: str,
    _: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, region)
    try:
        result = get_instance_live(account_id, instance_id, region)
        save_resource_snapshot(
            account_id=account_id,
            resource_type="INSTANCE_LIVE",
            resource_id=instance_id,
            region=region,
            payload=result,
        )
        return result
    except Exception as exc:
        raise service_error(exc, "读取实例实时信息失败") from exc


@router.put("/accounts/{account_id}/instances/{instance_id}/details")
def update_instance_route(
    account_id: int,
    instance_id: str,
    payload: InstanceUpdateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, payload.region)
    try:
        result = update_instance_details(
            account_id,
            instance_id,
            region=payload.region,
            display_name=payload.display_name,
            note=payload.note,
            ocpus=payload.ocpus,
            memory_in_gbs=payload.memory_in_gbs,
        )
    except Exception as exc:
        raise service_error(exc, "更新实例失败") from exc
    audit(user, request, "OCI_INSTANCE_DETAILS_UPDATED", "OCI_INSTANCE", instance_id, payload.model_dump_json())
    return result


@router.get("/accounts/{account_id}/instances/{instance_id}/network")
def network_inventory(
    account_id: int,
    instance_id: str,
    region: str,
    compartment_id: str,
    refresh: bool = False,
    _: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, region)
    if not refresh:
        cached = list_resource_cache(account_id, "VNIC", region=region)
        items = [row["payload"] for row in cached if row["payload"].get("instance_id") == instance_id]
        if items:
            return {"source": "cache", "items": items, "synced_at": cached[0]["synced_at"]}
    try:
        inventory = list_vnic_inventory(
            account_id,
            instance_id,
            region=region,
            compartment_id=compartment_id,
        )
    except Exception as exc:
        raise service_error(exc, "读取 VNIC 失败") from exc
    cache_items = []
    for index, item in enumerate(inventory):
        payload = dict(item)
        payload["instance_id"] = instance_id
        attachment_id = (payload.get("attachment") or {}).get("id")
        vnic_id = (payload.get("vnic") or {}).get("id")
        payload["cache_id"] = attachment_id or vnic_id or f"{instance_id}:{index}"
        cache_items.append(payload)
    replace_resource_type_cache(
        account_id=account_id,
        resource_type="VNIC",
        region=region,
        items=cache_items,
        id_field="cache_id",
    )
    return {"source": "oci", "items": inventory, "synced_at": datetime.now(timezone.utc).isoformat()}


@router.post("/accounts/{account_id}/instances/{instance_id}/vnics")
def create_vnics_route(
    account_id: int,
    instance_id: str,
    payload: SecondaryVnicCreateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> list[dict]:
    require_instance_region(account_id, instance_id, payload.region)
    require_prefix(payload.subnet_id, "ocid1.subnet.", "Subnet")
    try:
        result = create_secondary_vnics(
            account_id,
            instance_id,
            region=payload.region,
            compartment_id=payload.compartment_id,
            subnet_id=payload.subnet_id,
            count=payload.count,
            display_name=payload.display_name,
            assign_public_ip=payload.assign_public_ip,
            ipv6_count=payload.ipv6_count,
            skip_source_dest_check=payload.skip_source_dest_check,
        )
    except Exception as exc:
        raise service_error(exc, "创建附属 VNIC 失败") from exc
    audit(user, request, "OCI_VNIC_CREATED", "OCI_INSTANCE", instance_id, f"count={len(result)}")
    return result


@router.delete("/accounts/{account_id}/vnic-attachments/{attachment_id}")
def detach_vnic_route(
    account_id: int,
    attachment_id: str,
    region: str,
    confirmation: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(attachment_id, "ocid1.vnicattachment.", "VNIC Attachment")
    if confirmation != "DELETE VNIC":
        raise HTTPException(status_code=422, detail="请输入 DELETE VNIC 确认")
    try:
        detach_secondary_vnic(account_id, attachment_id, region=region)
    except Exception as exc:
        raise service_error(exc, "删除附属 VNIC 失败") from exc
    audit(user, request, "OCI_VNIC_DELETED", "OCI_VNIC_ATTACHMENT", attachment_id, region)
    return {"ok": True}


@router.post("/accounts/{account_id}/vnic-attachments/bulk-delete")
def bulk_detach_vnics_route(
    account_id: int,
    payload: BulkVnicDeleteRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    detached: list[str] = []
    errors: list[dict] = []
    for attachment_id in payload.attachment_ids:
        require_prefix(attachment_id, "ocid1.vnicattachment.", "VNIC Attachment")
        try:
            detach_secondary_vnic(account_id, attachment_id, region=payload.region)
            detached.append(attachment_id)
        except Exception as exc:
            errors.append({"attachment_id": attachment_id, "error": f"{type(exc).__name__}: {exc}"})
    audit(
        user, request, "OCI_VNICS_BULK_DELETED", "OCI_VNIC_ATTACHMENT", None,
        f"detached={len(detached)},errors={len(errors)}",
    )
    return {"ok": not errors, "detached": detached, "errors": errors}


@router.post("/accounts/{account_id}/vnics/{vnic_id}/ipv6")
def add_ipv6_route(
    account_id: int,
    vnic_id: str,
    payload: Ipv6CreateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> list[dict]:
    require_account(account_id)
    require_prefix(vnic_id, "ocid1.vnic.", "VNIC")
    try:
        result = add_ipv6_addresses(account_id, vnic_id, region=payload.region, count=payload.count)
    except Exception as exc:
        raise service_error(exc, "添加 IPv6 失败") from exc
    audit(user, request, "OCI_IPV6_CREATED", "OCI_VNIC", vnic_id, f"count={len(result)}")
    return result


@router.delete("/accounts/{account_id}/ipv6/{ipv6_id}")
def delete_ipv6_route(
    account_id: int,
    ipv6_id: str,
    region: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(ipv6_id, "ocid1.ipv6.", "IPv6")
    try:
        delete_ipv6_address(account_id, ipv6_id, region=region)
    except Exception as exc:
        raise service_error(exc, "删除 IPv6 失败") from exc
    audit(user, request, "OCI_IPV6_DELETED", "OCI_IPV6", ipv6_id, region)
    return {"ok": True}


@router.put("/accounts/{account_id}/vnics/{vnic_id}")
def update_vnic_route(
    account_id: int,
    vnic_id: str,
    payload: VnicUpdateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(vnic_id, "ocid1.vnic.", "VNIC")
    try:
        result = update_vnic_settings(
            account_id,
            vnic_id,
            region=payload.region,
            display_name=payload.display_name,
            hostname_label=payload.hostname_label,
            skip_source_dest_check=payload.skip_source_dest_check,
            nsg_ids=payload.nsg_ids,
        )
    except Exception as exc:
        raise service_error(exc, "更新 VNIC 失败") from exc
    audit(user, request, "OCI_VNIC_UPDATED", "OCI_VNIC", vnic_id, payload.model_dump_json())
    return result


@router.post("/accounts/{account_id}/instances/{instance_id}/network/recover")
def recover_network_route(
    account_id: int,
    instance_id: str,
    payload: NetworkInventoryRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, payload.region)
    try:
        result = recover_instance_network(
            account_id,
            instance_id,
            region=payload.region,
            compartment_id=payload.compartment_id,
        )
    except Exception as exc:
        raise service_error(exc, "恢复网络失败") from exc
    audit(user, request, "OCI_NETWORK_RECOVERED", "OCI_INSTANCE", instance_id, payload.region)
    return result


@router.post("/accounts/{account_id}/vnics/forwarding")
def forwarding_route(
    account_id: int,
    payload: ForwardingRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> list[dict]:
    require_account(account_id)
    for vnic_id in payload.vnic_ids:
        require_prefix(vnic_id, "ocid1.vnic.", "VNIC")
    try:
        result = configure_vnic_forwarding(
            account_id,
            payload.vnic_ids,
            region=payload.region,
            enabled=payload.enabled,
        )
    except Exception as exc:
        raise service_error(exc, "配置 VNIC 转发失败") from exc
    audit(user, request, "OCI_VNIC_FORWARDING_UPDATED", "OCI_VNIC", None, f"count={len(result)},enabled={payload.enabled}")
    return result


@router.post("/accounts/{account_id}/private-ips/{private_ip_id}/cidr-rotate")
def cidr_rotate_route(
    account_id: int,
    private_ip_id: str,
    payload: CidrRotateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, payload.instance_id, payload.region)
    require_prefix(private_ip_id, "ocid1.privateip.", "Private IP")
    try:
        result = rotate_public_ip_to_cidr(
            account_id,
            private_ip_id,
            region=payload.region,
            cidr=payload.cidr,
            max_rotations=payload.max_rotations,
        )
    except Exception as exc:
        raise service_error(exc, "指定网段换 IP 失败") from exc
    add_ip_quality_history(
        account_id=account_id,
        instance_id=payload.instance_id,
        private_ip_id=private_ip_id,
        public_ip=result.get("new_ip"),
        region=payload.region,
        score=None,
        payload=result,
        action="CIDR_ROTATE",
    )
    audit(user, request, "OCI_PUBLIC_IP_CIDR_ROTATED", "OCI_PRIVATE_IP", private_ip_id, json.dumps(result, ensure_ascii=False))
    return result


@router.post("/accounts/{account_id}/ip-quality/check")
def ip_quality_route(
    account_id: int,
    payload: IpQualityRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, payload.instance_id, payload.region)
    require_prefix(payload.private_ip_id, "ocid1.privateip.", "Private IP")
    try:
        result = check_public_ip_quality(payload.public_ip)
    except Exception as exc:
        raise service_error(exc, "IP 质量检测失败") from exc
    history = add_ip_quality_history(
        account_id=account_id,
        instance_id=payload.instance_id,
        private_ip_id=payload.private_ip_id,
        public_ip=payload.public_ip,
        region=payload.region,
        score=result.get("score"),
        payload=result,
        action="CHECK",
    )
    audit(user, request, "OCI_PUBLIC_IP_QUALITY_CHECKED", "OCI_PUBLIC_IP", payload.public_ip, f"score={result.get('score')}")
    return {**result, "history_id": history["id"]}


@router.post("/accounts/{account_id}/private-ips/{private_ip_id}/quality-rotate")
def quality_rotate_route(
    account_id: int,
    private_ip_id: str,
    payload: QualityRotateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, payload.instance_id, payload.region)
    require_prefix(private_ip_id, "ocid1.privateip.", "Private IP")
    attempts: list[dict] = []
    final: dict | None = None
    for _ in range(payload.max_rotations):
        try:
            rotated = rotate_public_ip_to_cidr(
                account_id,
                private_ip_id,
                region=payload.region,
                cidr=payload.cidr,
                max_rotations=1,
            )
            quality = check_public_ip_quality(rotated.get("new_ip"))
        except Exception as exc:
            raise service_error(exc, "优选 IP 失败") from exc
        item = {"rotation": rotated, "quality": quality}
        attempts.append(item)
        add_ip_quality_history(
            account_id=account_id,
            instance_id=payload.instance_id,
            private_ip_id=private_ip_id,
            public_ip=rotated.get("new_ip"),
            region=payload.region,
            score=quality.get("score"),
            payload=item,
            action="QUALITY_ROTATE",
        )
        final = item
        if rotated.get("matched") and int(quality.get("score") or 0) >= payload.minimum_score:
            break
    result = {
        "matched": bool(final and final["rotation"].get("matched") and int(final["quality"].get("score") or 0) >= payload.minimum_score),
        "minimum_score": payload.minimum_score,
        "attempts": attempts,
        "final": final,
    }
    audit(user, request, "OCI_PUBLIC_IP_QUALITY_ROTATED", "OCI_PRIVATE_IP", private_ip_id, f"matched={result['matched']},attempts={len(attempts)}")
    return result


@router.get("/accounts/{account_id}/ip-quality/history")
def ip_history(
    account_id: int,
    instance_id: str | None = None,
    limit: int = 200,
    _: dict = Depends(current_user),
) -> list[dict]:
    require_account(account_id)
    return list_ip_quality_history(account_id, instance_id=instance_id, limit=limit)


@router.get("/accounts/{account_id}/security-lists/{security_list_id}")
def security_list_route(
    account_id: int,
    security_list_id: str,
    region: str,
    _: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(security_list_id, "ocid1.securitylist.", "Security List")
    try:
        result = get_security_list(account_id, security_list_id, region=region)
    except Exception as exc:
        raise service_error(exc, "读取安全规则失败") from exc
    save_resource_snapshot(account_id=account_id, resource_type="SECURITY_LIST", resource_id=security_list_id, region=region, payload=result)
    return result


@router.put("/accounts/{account_id}/security-lists/{security_list_id}")
def security_list_update_route(
    account_id: int,
    security_list_id: str,
    payload: SecurityListUpdateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(security_list_id, "ocid1.securitylist.", "Security List")
    try:
        result = update_security_list_rules(
            account_id,
            security_list_id,
            region=payload.region,
            display_name=payload.display_name,
            ingress_rules=payload.ingress_rules,
            egress_rules=payload.egress_rules,
        )
    except Exception as exc:
        raise service_error(exc, "更新安全规则失败") from exc
    audit(user, request, "OCI_SECURITY_LIST_UPDATED", "OCI_SECURITY_LIST", security_list_id, f"ingress={len(payload.ingress_rules)},egress={len(payload.egress_rules)}")
    return result


@router.post("/accounts/{account_id}/security-lists/{security_list_id}/open-all")
def security_open_all_route(
    account_id: int,
    security_list_id: str,
    payload: RegionRequest,
    confirmation: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(security_list_id, "ocid1.securitylist.", "Security List")
    if confirmation != "OPEN ALL":
        raise HTTPException(status_code=422, detail="请输入 OPEN ALL 确认开放所有协议")
    try:
        result = open_all_security_rules(account_id, security_list_id, region=payload.region)
    except Exception as exc:
        raise service_error(exc, "开放所有协议失败") from exc
    audit(user, request, "OCI_SECURITY_LIST_OPEN_ALL", "OCI_SECURITY_LIST", security_list_id, payload.region)
    return result


@router.get("/accounts/{account_id}/boot-volumes")
def boot_volumes_route(
    account_id: int,
    region: str,
    compartment_id: str,
    availability_domain: str | None = None,
    refresh: bool = True,
    _: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if not refresh:
        rows = list_resource_cache(account_id, "BOOT_VOLUME", region=region)
        if rows:
            return {"source": "cache", "items": [item["payload"] for item in rows], "synced_at": rows[0]["synced_at"]}
    try:
        items = list_boot_volumes(
            account_id,
            region=region,
            compartment_id=compartment_id,
            availability_domain=availability_domain,
        )
    except Exception as exc:
        raise service_error(exc, "读取引导卷失败") from exc
    replace_resource_type_cache(account_id=account_id, resource_type="BOOT_VOLUME", region=region, items=items)
    return {"source": "oci", "items": items, "synced_at": datetime.now(timezone.utc).isoformat()}


@router.put("/accounts/{account_id}/boot-volumes/{volume_id}/details")
def update_boot_volume_route(
    account_id: int,
    volume_id: str,
    payload: BootVolumeUpdateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_boot_volume(account_id, volume_id)
    try:
        result = update_boot_volume(account_id, volume_id, payload.model_dump())
    except Exception as exc:
        raise service_error(exc, "更新引导卷失败") from exc
    audit(user, request, "OCI_BOOT_VOLUME_UPDATED", "OCI_BOOT_VOLUME", volume_id, payload.model_dump_json())
    return result


@router.delete("/accounts/{account_id}/boot-volumes/{volume_id}/permanent")
def delete_boot_volume_route(
    account_id: int,
    volume_id: str,
    region: str,
    confirmation: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_boot_volume(account_id, volume_id)
    if confirmation != "DELETE VOLUME":
        raise HTTPException(status_code=422, detail="请输入 DELETE VOLUME 确认")
    try:
        delete_boot_volume(account_id, volume_id, region=region)
    except Exception as exc:
        raise service_error(exc, "删除引导卷失败") from exc
    audit(user, request, "OCI_BOOT_VOLUME_DELETED", "OCI_BOOT_VOLUME", volume_id, region)
    return {"ok": True}


@router.get("/accounts/{account_id}/images")
def custom_images_route(
    account_id: int,
    region: str,
    compartment_id: str,
    refresh: bool = True,
    _: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if not refresh:
        rows = list_resource_cache(account_id, "CUSTOM_IMAGE", region=region)
        if rows:
            return {"source": "cache", "items": [item["payload"] for item in rows], "synced_at": rows[0]["synced_at"]}
    try:
        items = list_custom_images(account_id, region=region, compartment_id=compartment_id)
    except Exception as exc:
        raise service_error(exc, "读取自定义镜像失败") from exc
    replace_resource_type_cache(account_id=account_id, resource_type="CUSTOM_IMAGE", region=region, items=items)
    return {"source": "oci", "items": items, "synced_at": datetime.now(timezone.utc).isoformat()}


@router.delete("/accounts/{account_id}/images/{image_id}")
def delete_image_route(
    account_id: int,
    image_id: str,
    region: str,
    confirmation: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(image_id, "ocid1.image.", "Image")
    if confirmation != "DELETE IMAGE":
        raise HTTPException(status_code=422, detail="请输入 DELETE IMAGE 确认")
    try:
        delete_custom_image(account_id, image_id, region=region)
    except Exception as exc:
        raise service_error(exc, "删除镜像失败") from exc
    audit(user, request, "OCI_IMAGE_DELETED", "OCI_IMAGE", image_id, region)
    return {"ok": True}


@router.get("/accounts/{account_id}/regions")
def region_inventory_route(
    account_id: int,
    refresh: bool = True,
    _: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if not refresh:
        rows = list_resource_cache(account_id, "REGION_INVENTORY")
        if rows:
            return {"source": "cache", **rows[0]["payload"], "synced_at": rows[0]["synced_at"]}
    try:
        result = list_region_subscriptions(account_id)
    except Exception as exc:
        raise service_error(exc, "读取区域订阅失败") from exc
    save_resource_snapshot(account_id=account_id, resource_type="REGION_INVENTORY", resource_id="all", region=None, payload=result)
    return {"source": "oci", **result, "synced_at": datetime.now(timezone.utc).isoformat()}


@router.post("/accounts/{account_id}/regions/subscribe")
def subscribe_regions_route(
    account_id: int,
    payload: RegionSubscribeRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> list[dict]:
    require_account(account_id)
    try:
        result = subscribe_regions(account_id, payload.region_keys)
    except Exception as exc:
        raise service_error(exc, "订阅区域失败") from exc
    audit(user, request, "OCI_REGIONS_SUBSCRIBED", "OCI_ACCOUNT", str(account_id), ",".join(payload.region_keys))
    return result


@router.get("/accounts/{account_id}/iam")
def iam_inventory_route(account_id: int, refresh: bool = True, _: dict = Depends(current_user)) -> dict:
    require_account(account_id)
    if not refresh:
        rows = list_resource_cache(account_id, "IAM_INVENTORY")
        if rows:
            return {"source": "cache", **rows[0]["payload"], "synced_at": rows[0]["synced_at"]}
    try:
        result = list_iam_inventory(account_id)
    except Exception as exc:
        raise service_error(exc, "读取 OCI IAM 失败") from exc
    save_resource_snapshot(account_id=account_id, resource_type="IAM_INVENTORY", resource_id="all", region=None, payload=result)
    return {"source": "oci", **result, "synced_at": datetime.now(timezone.utc).isoformat()}


@router.post("/accounts/{account_id}/iam/users", status_code=201)
def create_iam_user_route(
    account_id: int,
    payload: IamUserCreateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if payload.group_id:
        require_prefix(payload.group_id, "ocid1.group.", "IAM Group")
    try:
        created_user = create_iam_user(
            account_id,
            name=payload.name,
            description=payload.description,
            email=payload.email,
        )
    except Exception as exc:
        raise service_error(exc, "创建 OCI IAM 用户失败") from exc

    response: dict = {
        "user": created_user,
        "membership": None,
        "temporary_password": None,
        "warnings": [],
    }
    iam_user_id = str(created_user.get("id") or "")
    if payload.group_id and iam_user_id:
        try:
            response["membership"] = add_iam_user_to_group(
                account_id,
                iam_user_id,
                payload.group_id,
            )
        except Exception as exc:
            response["warnings"].append(f"用户已创建，但加入用户组失败：{exc}")
    if payload.create_console_password and iam_user_id:
        try:
            password_result = reset_iam_console_password(account_id, iam_user_id)
            response["temporary_password"] = password_result.get("password")
            response["password_result"] = password_result
        except Exception as exc:
            response["warnings"].append(f"用户已创建，但生成临时密码失败：{exc}")

    audit(
        user,
        request,
        "OCI_IAM_USER_CREATED",
        "OCI_IAM_USER",
        iam_user_id,
        f"name={payload.name},group={'yes' if payload.group_id else 'no'},password={'yes' if payload.create_console_password else 'no'}",
    )
    return response


@router.put("/accounts/{account_id}/iam/users/{iam_user_id}/notification-email")
def update_iam_user_email_route(
    account_id: int,
    iam_user_id: str,
    payload: IamUserEmailUpdateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(iam_user_id, "ocid1.user.", "IAM User")
    try:
        result = update_iam_user_email(account_id, iam_user_id, payload.email)
    except Exception as exc:
        raise service_error(exc, "修改 OCI IAM 通知邮箱失败") from exc
    audit(
        user,
        request,
        "OCI_IAM_NOTIFICATION_EMAIL_UPDATED",
        "OCI_IAM_USER",
        iam_user_id,
        payload.email,
    )
    return result


@router.post("/accounts/{account_id}/iam/users/{iam_user_id}/reset-mfa")
def reset_iam_mfa_route(
    account_id: int,
    iam_user_id: str,
    payload: IamMfaResetRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(iam_user_id, "ocid1.user.", "IAM User")
    try:
        result = reset_iam_mfa(account_id, iam_user_id)
    except Exception as exc:
        raise service_error(exc, "重置 OCI IAM MFA 失败") from exc
    audit(
        user,
        request,
        "OCI_IAM_MFA_RESET",
        "OCI_IAM_USER",
        iam_user_id,
        f"deleted_devices={result.get('deleted_count', 0)}",
    )
    return result


@router.delete("/accounts/{account_id}/iam/users/{iam_user_id}")
def delete_iam_user_route(
    account_id: int,
    iam_user_id: str,
    confirmation: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(iam_user_id, "ocid1.user.", "IAM User")
    if confirmation != "DELETE USER":
        raise HTTPException(status_code=422, detail="请输入 DELETE USER 确认")
    try:
        delete_iam_user(account_id, iam_user_id)
    except Exception as exc:
        raise service_error(exc, "删除 OCI IAM 用户失败") from exc
    audit(user, request, "OCI_IAM_USER_DELETED", "OCI_IAM_USER", iam_user_id)
    return {"ok": True}


@router.post("/accounts/{account_id}/iam/memberships")
def add_membership_route(
    account_id: int,
    payload: MembershipRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        result = add_iam_user_to_group(account_id, payload.user_id, payload.group_id)
    except Exception as exc:
        raise service_error(exc, "分配 OCI 用户组失败") from exc
    audit(user, request, "OCI_IAM_MEMBERSHIP_CREATED", "OCI_IAM_MEMBERSHIP", result.get("id"), f"user={payload.user_id},group={payload.group_id}")
    return result


@router.delete("/accounts/{account_id}/iam/memberships/{membership_id}")
def remove_membership_route(
    account_id: int,
    membership_id: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(membership_id, "ocid1.groupmembership.", "Group Membership")
    try:
        remove_iam_membership(account_id, membership_id)
    except Exception as exc:
        raise service_error(exc, "移除 OCI 用户组失败") from exc
    audit(user, request, "OCI_IAM_MEMBERSHIP_DELETED", "OCI_IAM_MEMBERSHIP", membership_id)
    return {"ok": True}


@router.post("/accounts/{account_id}/iam/users/{iam_user_id}/reset-password")
def reset_password_route(
    account_id: int,
    iam_user_id: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    require_prefix(iam_user_id, "ocid1.user.", "IAM User")
    try:
        result = reset_iam_console_password(account_id, iam_user_id)
    except Exception as exc:
        raise service_error(exc, "重置 OCI 控制台密码失败") from exc
    # The generated password is returned only in this response and never logged.
    audit(user, request, "OCI_IAM_PASSWORD_RESET", "OCI_IAM_USER", iam_user_id, "temporary_password_returned_once")
    return result


@router.get("/accounts/{account_id}/iam/password-policy")
def password_policy_route(account_id: int, _: dict = Depends(current_user)) -> dict:
    require_account(account_id)
    try:
        return get_authentication_policy(account_id)
    except Exception as exc:
        raise service_error(exc, "读取 OCI 密码策略失败") from exc


@router.put("/accounts/{account_id}/iam/password-policy")
def update_password_policy_route(
    account_id: int,
    payload: PasswordPolicyRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        result = update_authentication_policy(account_id, payload.model_dump(exclude_none=True))
    except Exception as exc:
        raise service_error(exc, "更新 OCI 密码策略失败") from exc
    audit(user, request, "OCI_IAM_PASSWORD_POLICY_UPDATED", "OCI_ACCOUNT", str(account_id), payload.model_dump_json(exclude_none=True))
    return result


@router.get("/accounts/{account_id}/limits")
def limits_route(
    account_id: int,
    service_name: str | None = None,
    refresh: bool = True,
    _: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    cache_id = service_name or "all"
    if not refresh:
        rows = list_resource_cache(account_id, "LIMITS")
        for row in rows:
            if row["resource_id"] == cache_id:
                return {"source": "cache", **row["payload"], "synced_at": row["synced_at"]}
    try:
        result = list_limits(account_id, service_name)
    except Exception as exc:
        raise service_error(exc, "读取 OCI 配额失败") from exc
    save_resource_snapshot(account_id=account_id, resource_type="LIMITS", resource_id=cache_id, region=None, payload=result)
    return {"source": "oci", **result, "synced_at": datetime.now(timezone.utc).isoformat()}


@router.get("/accounts/{account_id}/oci-audit")
def oci_audit_route(
    account_id: int,
    region: str | None = None,
    compartment_id: str | None = None,
    hours: int = 24,
    limit: int = 100,
    _: dict = Depends(current_user),
) -> list[dict]:
    account = require_account(account_id)
    effective_region = (region or account.get("home_region_key") or account.get("region") or "").strip()
    effective_compartment = (compartment_id or account.get("tenancy_ocid") or "").strip()
    if not effective_region:
        raise HTTPException(status_code=422, detail="无法确定 OCI 审计区域")
    if not effective_compartment.startswith("ocid1."):
        raise HTTPException(status_code=422, detail="无法确定 OCI 审计 Compartment；默认应为租户根区间 OCID")
    try:
        return list_oci_audit_events(
            account_id,
            region=effective_region,
            compartment_id=effective_compartment,
            hours=hours,
            limit=limit,
        )
    except Exception as exc:
        raise service_error(exc, "读取 OCI 审计日志失败") from exc


@router.get("/accounts/{account_id}/instances/{instance_id}/metrics")
def metrics_route(
    account_id: int,
    instance_id: str,
    region: str,
    compartment_id: str,
    hours: int = 24,
    cached: bool = False,
    _: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, region)

    if cached:
        snapshot = get_resource_cache(
            account_id,
            "METRICS",
            f"{instance_id}:{hours}",
        )
        if snapshot and isinstance(snapshot.get("payload"), dict):
            result = dict(snapshot["payload"])
            result["from_cache"] = True
            result["cached_at"] = snapshot.get("synced_at")
            return result

        raise HTTPException(
            status_code=404,
            detail="暂无实例指标缓存",
        )

    try:
        result = query_instance_metrics(
            account_id,
            region=region,
            compartment_id=compartment_id,
            instance_id=instance_id,
            hours=hours,
        )
    except Exception as exc:
        raise service_error(exc, "读取实例指标失败") from exc

    result["from_cache"] = False

    save_resource_snapshot(
        account_id=account_id,
        resource_type="METRICS",
        resource_id=f"{instance_id}:{hours}",
        region=region,
        payload=result,
    )
    return result


@router.post("/accounts/{account_id}/costs")
def costs_route(
    account_id: int,
    payload: CostQueryRequest,
    _: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if payload.end_date <= payload.start_date:
        raise HTTPException(status_code=422, detail="结束时间必须晚于开始时间")
    if payload.end_date - payload.start_date > timedelta(days=366):
        raise HTTPException(status_code=422, detail="单次费用查询不能超过 366 天")
    try:
        result = query_costs(account_id, start_date=payload.start_date, end_date=payload.end_date)
    except Exception as exc:
        raise service_error(exc, "读取 OCI 费用失败") from exc
    save_resource_snapshot(account_id=account_id, resource_type="COST", resource_id=f"{payload.start_date.date()}_{payload.end_date.date()}", region=None, payload=result)
    return result


@router.get("/accounts/{account_id}/object-storage")
def storage_overview_route(
    account_id: int,
    region: str,
    compartment_id: str,
    refresh: bool = True,
    _: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if not refresh:
        rows = list_resource_cache(account_id, "OBJECT_STORAGE", region=region)
        if rows:
            return {"source": "cache", **rows[0]["payload"], "synced_at": rows[0]["synced_at"]}
    try:
        result = object_storage_overview(account_id, region=region, compartment_id=compartment_id)
    except Exception as exc:
        raise service_error(exc, "读取对象存储失败") from exc
    save_resource_snapshot(account_id=account_id, resource_type="OBJECT_STORAGE", resource_id="overview", region=region, payload=result)
    return {"source": "oci", **result, "synced_at": datetime.now(timezone.utc).isoformat()}


@router.post("/accounts/{account_id}/object-storage/buckets", status_code=201)
def create_bucket_route(
    account_id: int,
    payload: BucketCreateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        result = create_bucket(account_id, **payload.model_dump())
    except Exception as exc:
        raise service_error(exc, "创建存储桶失败") from exc
    audit(user, request, "OCI_BUCKET_CREATED", "OCI_BUCKET", payload.name, payload.region)
    return result


@router.put("/accounts/{account_id}/object-storage/buckets/{bucket_name}")
def update_bucket_route(
    account_id: int,
    bucket_name: str,
    payload: BucketAccessRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        result = update_bucket_access(account_id, region=payload.region, namespace=payload.namespace, bucket_name=bucket_name, public_access_type=payload.public_access_type)
    except Exception as exc:
        raise service_error(exc, "修改存储桶访问类型失败") from exc
    audit(user, request, "OCI_BUCKET_UPDATED", "OCI_BUCKET", bucket_name, payload.public_access_type)
    return result


@router.delete("/accounts/{account_id}/object-storage/buckets/{bucket_name}")
def delete_bucket_route(
    account_id: int,
    bucket_name: str,
    region: str,
    namespace: str,
    confirmation: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if confirmation != "DELETE BUCKET":
        raise HTTPException(status_code=422, detail="请输入 DELETE BUCKET 确认")
    try:
        delete_bucket(account_id, region=region, namespace=namespace, bucket_name=bucket_name)
    except Exception as exc:
        raise service_error(exc, "删除存储桶失败") from exc
    audit(user, request, "OCI_BUCKET_DELETED", "OCI_BUCKET", bucket_name, region)
    return {"ok": True}


@router.get("/accounts/{account_id}/object-storage/buckets/{bucket_name}/objects")
def list_objects_route(
    account_id: int,
    bucket_name: str,
    region: str,
    namespace: str,
    prefix: str | None = None,
    delimiter: str | None = "/",
    start: str | None = None,
    limit: int = 100,
    _: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        return list_objects(account_id, region=region, namespace=namespace, bucket_name=bucket_name, prefix=prefix, delimiter=delimiter, start=start, limit=limit)
    except Exception as exc:
        raise service_error(exc, "读取对象失败") from exc


@router.post("/accounts/{account_id}/object-storage/buckets/{bucket_name}/objects")
async def upload_object_route(
    account_id: int,
    bucket_name: str,
    request: Request,
    region: str = Form(...),
    namespace: str = Form(...),
    object_name: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    data = await file.read(100 * 1024 * 1024 + 1)
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单文件直接上传上限为 100 MB；更大文件请使用分片上传")
    try:
        result = put_object(account_id, region=region, namespace=namespace, bucket_name=bucket_name, object_name=object_name, data=data, content_length=len(data), content_type=file.content_type)
    except Exception as exc:
        raise service_error(exc, "上传对象失败") from exc
    audit(user, request, "OCI_OBJECT_UPLOADED", "OCI_OBJECT", object_name, f"bucket={bucket_name},size={len(data)}")
    return result


@router.get("/accounts/{account_id}/object-storage/buckets/{bucket_name}/objects/content")
def download_object_route(
    account_id: int,
    bucket_name: str,
    region: str,
    namespace: str,
    object_name: str,
    disposition: Literal["inline", "attachment"] = "attachment",
    _: dict = Depends(current_user),
) -> Response:
    require_account(account_id)
    try:
        content, headers = get_object(account_id, region=region, namespace=namespace, bucket_name=bucket_name, object_name=object_name)
    except Exception as exc:
        raise service_error(exc, "下载对象失败") from exc
    media_type = headers.get("content-type") or "application/octet-stream"
    response_headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(object_name.split('/')[-1])}",
        "Cache-Control": "private, no-store",
    }
    return Response(content=content, media_type=media_type, headers=response_headers)


@router.delete("/accounts/{account_id}/object-storage/buckets/{bucket_name}/objects")
def delete_object_route(
    account_id: int,
    bucket_name: str,
    region: str,
    namespace: str,
    object_name: str,
    confirmation: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if confirmation != "DELETE OBJECT":
        raise HTTPException(status_code=422, detail="请输入 DELETE OBJECT 确认")
    try:
        delete_object(account_id, region=region, namespace=namespace, bucket_name=bucket_name, object_name=object_name)
    except Exception as exc:
        raise service_error(exc, "删除对象失败") from exc
    audit(user, request, "OCI_OBJECT_DELETED", "OCI_OBJECT", object_name, f"bucket={bucket_name}")
    return {"ok": True}


@router.post("/accounts/{account_id}/object-storage/buckets/{bucket_name}/preauth")
def preauth_route(
    account_id: int,
    bucket_name: str,
    payload: PreauthRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if payload.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="预签名链接过期时间必须晚于当前时间")
    try:
        result = create_preauthenticated_request(account_id, region=payload.region, namespace=payload.namespace, bucket_name=bucket_name, object_name=payload.object_name, name=payload.name, access_type=payload.access_type, expires_at=payload.expires_at)
    except Exception as exc:
        raise service_error(exc, "创建预签名链接失败") from exc
    audit(user, request, "OCI_PREAUTH_CREATED", "OCI_BUCKET", bucket_name, payload.name)
    return result


@router.post("/accounts/{account_id}/object-storage/multipart", status_code=201)
def multipart_create_route(
    account_id: int,
    payload: MultipartCreateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        created = create_multipart_upload(account_id, **payload.model_dump())
        session = create_multipart_session(account_id=account_id, region=payload.region, namespace=payload.namespace, bucket_name=payload.bucket_name, object_name=payload.object_name, upload_id=created["upload_id"])
    except Exception as exc:
        raise service_error(exc, "初始化分片上传失败") from exc
    audit(user, request, "OCI_MULTIPART_CREATED", "OCI_OBJECT", payload.object_name, f"session={session['id']}")
    return session


@router.post("/accounts/{account_id}/object-storage/multipart/{session_id}/parts/{part_number}")
async def multipart_part_route(
    account_id: int,
    session_id: int,
    part_number: int,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    session = get_multipart_session(session_id)
    if not session or int(session["account_id"]) != int(account_id) or session["status"] != "ACTIVE":
        raise HTTPException(status_code=404, detail="分片上传会话不存在或已经结束")
    if part_number < 1 or part_number > 10000:
        raise HTTPException(status_code=422, detail="分片编号必须是 1–10000")
    data = await file.read(256 * 1024 * 1024 + 1)
    if len(data) > 256 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单个分片不能超过 256 MB")
    try:
        etag = upload_multipart_part(account_id, region=session["region"], namespace=session["namespace"], bucket_name=session["bucket_name"], object_name=session["object_name"], upload_id=session["upload_id"], part_number=part_number, data=data, content_length=len(data))
        updated = save_multipart_part(session_id, part_number, etag)
    except Exception as exc:
        raise service_error(exc, "上传分片失败") from exc
    audit(user, request, "OCI_MULTIPART_PART_UPLOADED", "OCI_OBJECT", session["object_name"], f"session={session_id},part={part_number}")
    return updated


@router.post("/accounts/{account_id}/object-storage/multipart/{session_id}/commit")
def multipart_commit_route(
    account_id: int,
    session_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    session = get_multipart_session(session_id)
    if not session or int(session["account_id"]) != int(account_id) or session["status"] != "ACTIVE":
        raise HTTPException(status_code=404, detail="分片上传会话不存在或已经结束")
    if not session["parts"]:
        raise HTTPException(status_code=422, detail="尚未上传任何分片")
    try:
        result = commit_multipart_upload(account_id, region=session["region"], namespace=session["namespace"], bucket_name=session["bucket_name"], object_name=session["object_name"], upload_id=session["upload_id"], parts=session["parts"])
        finish_multipart_session(session_id, "COMPLETED")
    except Exception as exc:
        raise service_error(exc, "提交分片上传失败") from exc
    audit(user, request, "OCI_MULTIPART_COMMITTED", "OCI_OBJECT", session["object_name"], f"session={session_id}")
    return result


@router.post("/accounts/{account_id}/object-storage/multipart/{session_id}/abort")
def multipart_abort_route(
    account_id: int,
    session_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    session = get_multipart_session(session_id)
    if not session or int(session["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="分片上传会话不存在")
    try:
        if session["status"] == "ACTIVE":
            abort_multipart_upload(account_id, region=session["region"], namespace=session["namespace"], bucket_name=session["bucket_name"], object_name=session["object_name"], upload_id=session["upload_id"])
        finish_multipart_session(session_id, "ABORTED")
    except Exception as exc:
        raise service_error(exc, "取消分片上传失败") from exc
    audit(user, request, "OCI_MULTIPART_ABORTED", "OCI_OBJECT", session["object_name"], f"session={session_id}")
    return {"ok": True}


@router.get("/accounts/{account_id}/object-storage/multipart")
def multipart_list_route(account_id: int, active_only: bool = False, _: dict = Depends(current_user)) -> list[dict]:
    require_account(account_id)
    return list_multipart_sessions(account_id, active_only=active_only)


@router.get("/accounts/{account_id}/launch/profiles")
def profiles_route(account_id: int, _: dict = Depends(current_user)) -> list[dict]:
    require_account(account_id)
    return list_launch_profiles(account_id)


@router.put("/accounts/{account_id}/launch/profiles")
def bulk_update_profiles_route(
    account_id: int,
    payload: LaunchProfilesBulkRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    count = set_launch_profiles_enabled(account_id, payload.enabled)
    audit(user, request, "OCI_LAUNCH_PROFILES_BULK_UPDATED", "OCI_ACCOUNT", str(account_id), f"enabled={payload.enabled},count={count}")
    return {"ok": True, "count": count, "enabled": payload.enabled}


@router.post("/accounts/{account_id}/launch/profiles", status_code=201)
def create_profile_route(
    account_id: int,
    payload: LaunchProfileRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    result = create_launch_profile(account_id=account_id, name=payload.name, payload=payload.payload)
    if not payload.enabled:
        result = update_launch_profile(result["id"], enabled=False)
    audit(user, request, "OCI_LAUNCH_PROFILE_CREATED", "OCI_LAUNCH_PROFILE", str(result["id"]), payload.name)
    return result


@router.put("/accounts/{account_id}/launch/profiles/{profile_id}")
def update_profile_route(
    account_id: int,
    profile_id: int,
    payload: LaunchProfileUpdateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    current = get_launch_profile(profile_id)
    if not current or int(current["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="抢机配置不存在或不属于当前租户")
    try:
        result = update_launch_profile(profile_id, name=payload.name, payload=payload.payload, enabled=payload.enabled)
    except Exception as exc:
        raise service_error(exc, "更新抢机配置失败") from exc
    audit(user, request, "OCI_LAUNCH_PROFILE_UPDATED", "OCI_LAUNCH_PROFILE", str(profile_id), payload.model_dump_json(exclude_none=True))
    return result


@router.post("/accounts/{account_id}/launch/profiles/{profile_id}/clone", status_code=201)
def clone_profile_route(
    account_id: int,
    profile_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    current = get_launch_profile(profile_id)
    if not current or int(current["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="抢机配置不存在或不属于当前租户")
    result = create_launch_profile(account_id=account_id, name=f"{current['name']} - 副本", payload=current["payload"])
    audit(user, request, "OCI_LAUNCH_PROFILE_CLONED", "OCI_LAUNCH_PROFILE", str(result["id"]), f"from={profile_id}")
    return result


@router.post("/accounts/{account_id}/launch/profiles/{profile_id}/copy", status_code=201)
def copy_profile_to_accounts_route(
    account_id: int,
    profile_id: int,
    payload: LaunchProfileCopyRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    current = get_launch_profile(profile_id)
    if not current or int(current["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="抢机配置不存在或不属于当前租户")

    target_ids = list(dict.fromkeys(int(item) for item in payload.target_account_ids))
    target_ids = [item for item in target_ids if item != int(account_id)]
    if not target_ids:
        raise HTTPException(status_code=422, detail="请选择至少一个其他租户")

    template = launch_profile_template(current.get("payload"))
    created: list[dict] = []
    errors: list[dict] = []
    for target_id in target_ids:
        try:
            target = require_account(target_id)
            result = create_launch_profile(
                account_id=target_id,
                name=current["name"],
                payload=template,
            )
            if not payload.enabled:
                result = update_launch_profile(result["id"], enabled=False)
            created.append({
                "account_id": target_id,
                "account_name": target.get("custom_name") or target.get("tenancy_name") or str(target_id),
                "profile_id": result["id"],
                "name": result["name"],
                "enabled": result["enabled"],
            })
        except Exception as exc:
            errors.append({
                "account_id": target_id,
                "error": str(getattr(exc, "detail", None) or exc),
            })

    audit(
        user, request, "OCI_LAUNCH_PROFILE_COPIED_TO_ACCOUNTS",
        "OCI_LAUNCH_PROFILE", str(profile_id),
        f"created={len(created)},failed={len(errors)}",
    )
    return {
        "ok": bool(created) and not errors,
        "source_account_id": account_id,
        "source_profile_id": profile_id,
        "created_count": len(created),
        "failed_count": len(errors),
        "created": created,
        "errors": errors,
    }


@router.post("/accounts/{account_id}/launch/profiles/{profile_id}/preflight")
def preflight_profile_route(
    account_id: int,
    profile_id: int,
    payload: LaunchProfilePreflightRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    current = get_launch_profile(profile_id)
    if not current or int(current["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="抢机配置不存在或不属于当前租户")
    try:
        result = preflight_launch_payload(account_id, current.get("payload"))
        if payload.apply and result["ok"]:
            result["profile"] = update_launch_profile(profile_id, payload=result["payload"])
        result["profile_id"] = profile_id
    except Exception as exc:
        raise service_error(exc, "开机配置预检失败") from exc
    audit(
        user, request, "OCI_LAUNCH_PROFILE_PREFLIGHT",
        "OCI_LAUNCH_PROFILE", str(profile_id),
        f"ok={result.get('ok')},changed={len(result.get('changed_fields') or [])}",
    )
    return result


@router.delete("/accounts/{account_id}/launch/profiles/{profile_id}")
def delete_profile_route(
    account_id: int,
    profile_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    current = get_launch_profile(profile_id)
    if not current or int(current["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="抢机配置不存在或不属于当前租户")
    delete_launch_profile(profile_id)
    audit(user, request, "OCI_LAUNCH_PROFILE_DELETED", "OCI_LAUNCH_PROFILE", str(profile_id), current["name"])
    return {"ok": True}


@router.post("/accounts/{account_id}/launch/profiles/{profile_id}/run", status_code=202)
async def run_profile_route(
    account_id: int,
    profile_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    profile = get_launch_profile(profile_id)
    if not profile or int(profile["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="抢机配置不存在或不属于当前租户")
    if not profile["enabled"]:
        raise HTTPException(status_code=409, detail="抢机配置已停用")
    try:
        preflight = require_preflight_launch_payload(account_id, profile.get("payload"))
        update_launch_profile(profile_id, payload=preflight["payload"])
    except Exception as exc:
        raise service_error(exc, "开机配置资源预检失败") from exc
    data = dict(preflight["payload"])
    mode = str(data.pop("mode", "CAPACITY_RETRY")).upper()
    if mode == "RETRY":
        mode = "CAPACITY_RETRY"
    requested_count = int(data.pop("requested_count", 1))
    data.pop("max_attempts", None)
    mode = "CAPACITY_RETRY"
    max_attempts = 0
    retry_interval_seconds = int(data.pop("retry_interval_seconds", 30))
    concurrency = int(data.pop("concurrency", 1))
    try:
        result = await start_launch_task(account_id=account_id, requested_by=user["username"], ip_address=client_ip(request), mode=mode, request_payload=data, requested_count=requested_count, max_attempts=max_attempts, retry_interval_seconds=retry_interval_seconds, concurrency=concurrency, trusted_saved_request=True)
    except LaunchBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise service_error(exc, "启动抢机配置失败") from exc
    audit(user, request, "OCI_LAUNCH_PROFILE_RUN", "OCI_LAUNCH_PROFILE", str(profile_id), f"job={result.get('id')}")
    return result


@router.post("/accounts/{account_id}/launch/profiles/{profile_id}/run-once", status_code=202)
async def run_profile_once_route(
    account_id: int,
    profile_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    profile = get_launch_profile(profile_id)
    if not profile or int(profile["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="抢机配置不存在或不属于当前租户")
    try:
        preflight = require_preflight_launch_payload(account_id, profile.get("payload"))
        update_launch_profile(profile_id, payload=preflight["payload"])
    except Exception as exc:
        raise service_error(exc, "开机配置资源预检失败") from exc
    data = dict(preflight["payload"])
    data.pop("mode", None)
    requested_count = int(data.pop("requested_count", 1))
    data.pop("max_attempts", None)
    data.pop("retry_interval_seconds", None)
    concurrency = int(data.pop("concurrency", 1))
    try:
        result = await start_launch_task(
            account_id=account_id, requested_by=user["username"], ip_address=client_ip(request),
            mode="CREATE", request_payload=data, requested_count=requested_count,
            max_attempts=1, retry_interval_seconds=30, concurrency=concurrency,
            trusted_saved_request=True,
        )
    except LaunchBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise service_error(exc, "单次执行抢机配置失败") from exc
    audit(user, request, "OCI_LAUNCH_PROFILE_RUN_ONCE", "OCI_LAUNCH_PROFILE", str(profile_id), f"job={result.get('id')}")
    return result


@router.post("/accounts/{account_id}/launch/actions/cancel-active")
def cancel_active_launch_jobs_route(
    account_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    active = [job for job in list_launch_jobs(account_id, limit=200) if job.get("is_active")]
    cancelled = [int(job["id"]) for job in active if cancel_launch_task(int(job["id"]), account_id)]
    audit(user, request, "OCI_LAUNCH_JOBS_CANCELLED", "OCI_ACCOUNT", str(account_id), f"count={len(cancelled)}")
    return {"ok": True, "job_ids": cancelled, "count": len(cancelled)}


@router.post("/accounts/{account_id}/launch/actions/reset-history")
def reset_launch_history_route(
    account_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    reset_ids: list[int] = []
    for job in list_launch_jobs(account_id, limit=200):
        if not job.get("is_active"):
            reset_launch_job_counters(int(job["id"]), account_id)
            reset_ids.append(int(job["id"]))
    audit(user, request, "OCI_LAUNCH_HISTORY_RESET", "OCI_ACCOUNT", str(account_id), f"count={len(reset_ids)}")
    return {"ok": True, "job_ids": reset_ids, "count": len(reset_ids)}


@router.post("/accounts/{account_id}/launch/jobs/{job_id}/reset")
def reset_launch_job_route(
    account_id: int,
    job_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    job = get_launch_job(job_id)
    if not job or int(job["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="任务不存在或不属于当前租户")
    try:
        result = reset_launch_job_counters(job_id, account_id)
    except Exception as exc:
        raise service_error(exc, "重置任务统计失败") from exc
    audit(user, request, "OCI_LAUNCH_JOB_RESET", "OCI_LAUNCH_JOB", str(job_id))
    return result


@router.post("/accounts/{account_id}/launch/jobs/{job_id}/clone", status_code=202)
async def clone_job_route(
    account_id: int,
    job_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    job = get_launch_job(job_id)
    if not job or int(job["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="任务不存在或不属于当前租户")
    try:
        preflight = require_preflight_launch_payload(account_id, job["request"])
        result = await start_launch_task(account_id=account_id, requested_by=user["username"], ip_address=client_ip(request), mode="CAPACITY_RETRY", request_payload=preflight["payload"], requested_count=job["requested_count"], max_attempts=0, retry_interval_seconds=job["retry_interval_seconds"], concurrency=job["concurrency"], trusted_saved_request=True)
    except LaunchBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise service_error(exc, "复制任务失败") from exc
    audit(user, request, "OCI_LAUNCH_JOB_CLONED", "OCI_LAUNCH_JOB", str(result.get("id")), f"from={job_id}")
    return result


@router.post("/accounts/{account_id}/launch/jobs/{job_id}/retry-failed", status_code=202)
async def retry_failed_launch_job_route(
    account_id: int,
    job_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    job = get_launch_job(job_id)
    if not job or int(job["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="任务不存在或不属于当前租户")
    if job.get("is_active"):
        raise HTTPException(status_code=409, detail="运行中的任务不能重试失败项")
    remaining = max(
        0,
        int(job.get("requested_count") or 0) - int(job.get("success_count") or 0),
    )
    if remaining <= 0:
        raise HTTPException(status_code=409, detail="当前任务没有需要重试的失败配置")
    try:
        preflight = require_preflight_launch_payload(account_id, job["request"])
        result = await start_launch_task(
            account_id=account_id,
            requested_by=user["username"],
            ip_address=client_ip(request),
            mode="CAPACITY_RETRY",
            request_payload=preflight["payload"],
            requested_count=remaining,
            max_attempts=0,
            retry_interval_seconds=job["retry_interval_seconds"],
            concurrency=job["concurrency"],
            trusted_saved_request=True,
        )
    except LaunchBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise service_error(exc, "只重试失败配置失败") from exc
    audit(
        user, request, "OCI_LAUNCH_JOB_FAILED_ITEMS_RETRIED",
        "OCI_LAUNCH_JOB", str(result.get("id")),
        f"from={job_id},remaining={remaining}",
    )
    return {**result, "source_job_id": job_id, "retried_count": remaining}


@router.post("/accounts/{account_id}/instances/{instance_id}/vnc/sessions", status_code=201)
async def vnc_start_route(
    account_id: int,
    instance_id: str,
    payload: VncStartRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, payload.region)
    try:
        result = await start_vnc_session(account_id=account_id, instance_id=instance_id, region=payload.region, duration_minutes=payload.duration_minutes)
    except Exception as exc:
        raise service_error(exc, "启动浏览器 VNC 失败") from exc
    audit(user, request, "OCI_VNC_SESSION_STARTED", "OCI_INSTANCE", instance_id, f"session={result['id']}")
    return result


@router.get("/accounts/{account_id}/vnc/sessions")
def vnc_sessions_route(
    account_id: int,
    instance_id: str | None = None,
    _: dict = Depends(current_user),
) -> list[dict]:
    require_account(account_id)
    return list_vnc_sessions(account_id, instance_id)


@router.delete("/accounts/{account_id}/vnc/sessions/{session_id}")
async def vnc_stop_route(
    account_id: int,
    session_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    sessions = {int(item["id"]): item for item in list_vnc_sessions(account_id)}
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="VNC 会话不存在或不属于当前租户")
    await stop_vnc_session(session_id)
    audit(user, request, "OCI_VNC_SESSION_STOPPED", "OCI_VNC_SESSION", str(session_id))
    return {"ok": True}


@router.websocket("/vnc/ws/{token}")
async def vnc_websocket(websocket: WebSocket, token: str) -> None:
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    selected_protocol = "binary" if "binary" in [item.strip() for item in protocols.split(",")] else None
    try:
        _, port = resolve_vnc_target(token)
    except Exception:
        await websocket.close(code=4404)
        return
    await websocket.accept(subprotocol=selected_protocol)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        await websocket.close(code=1011)
        return

    async def browser_to_vnc() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data is None and message.get("text") is not None:
                data = message["text"].encode("latin-1", errors="ignore")
            if data:
                writer.write(data)
                await writer.drain()

    async def vnc_to_browser() -> None:
        while True:
            data = await reader.read(65536)
            if not data:
                return
            await websocket.send_bytes(data)

    tasks = [asyncio.create_task(browser_to_vnc()), asyncio.create_task(vnc_to_browser())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    except (WebSocketDisconnect, ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
