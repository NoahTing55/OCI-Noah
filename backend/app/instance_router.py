from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field

from .database import add_audit_log, get_user_by_username
from .instance_cache_repository import (
    update_cached_boot_volume,
    update_cached_instance,
)

from .oci_advanced_service import (
    create_boot_volume_backup,
    create_console_connection,
    create_instance_image,
    delete_boot_volume_backup,
    delete_console_connection,
    list_boot_volume_backups,
    list_console_connections,
    terminate_instance,
    update_boot_volume,
    update_instance,
)
from .oci_service import instance_action
from .auth_session_service import validate_access_token
from .tenant_scope import (
    require_account,
    require_boot_volume,
    require_instance_region,
)


router = APIRouter(prefix="/api/v1", tags=["OCI-N&T instances"])
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
    return request.client.host if request.client else None



def audit(
    user: dict,
    request: Request,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: str,
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
    return HTTPException(
        status_code=400,
        detail=f"{prefix}：{type(exc).__name__}: {exc}",
    )




class InstanceUpdateRequest(BaseModel):
    region: str
    display_name: str | None = Field(default=None, max_length=255)
    shape: str | None = None
    ocpus: float | None = Field(default=None, gt=0, le=160)
    memory_in_gbs: float | None = Field(default=None, gt=0, le=2048)


class TerminateRequest(BaseModel):
    region: str
    preserve_boot_volume: bool = True
    confirmation: str


class ConsoleRequest(BaseModel):
    region: str
    compartment_id: str
    public_key: str = Field(min_length=40, max_length=16384)


class BootVolumeUpdateRequest(BaseModel):
    region: str
    display_name: str | None = Field(default=None, max_length=255)
    size_in_gbs: int | None = Field(default=None, ge=50, le=32768)
    vpus_per_gb: int | None = Field(default=None, ge=0, le=120)
    is_auto_tune_enabled: bool | None = None


class BootVolumeBackupRequest(BaseModel):
    region: str
    display_name: str | None = Field(default=None, max_length=255)
    type: str = "INCREMENTAL"


class InstanceImageRequest(BaseModel):
    region: str
    compartment_id: str
    display_name: str = Field(min_length=1, max_length=255)






























class RescueRequest(BaseModel):
    region: str
    compartment_id: str
    public_key: str = Field(min_length=40, max_length=16384)
    confirmation: str














@router.put("/accounts/{account_id}/instances/{instance_id}")
def edit_instance(
    account_id: int,
    instance_id: str,
    payload: InstanceUpdateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, payload.region)
    try:
        result = update_instance(account_id, instance_id, **payload.model_dump())
    except Exception as exc:
        raise service_error(exc, "更新实例失败") from exc
    update_cached_instance(account_id, instance_id, result)
    audit(user, request, "OCI_INSTANCE_UPDATED", "OCI_INSTANCE", instance_id, payload.model_dump_json())
    return result


@router.post("/accounts/{account_id}/instances/{instance_id}/terminate")
def terminate(
    account_id: int,
    instance_id: str,
    payload: TerminateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, payload.region)
    if payload.confirmation != "TERMINATE":
        raise HTTPException(status_code=422, detail="请输入 TERMINATE 确认终止实例")
    try:
        result = terminate_instance(
            account_id,
            instance_id,
            region=payload.region,
            preserve_boot_volume=payload.preserve_boot_volume,
        )
    except Exception as exc:
        raise service_error(exc, "终止实例失败") from exc
    update_cached_instance(
        account_id,
        instance_id,
        {"lifecycle_state": "TERMINATING"},
    )
    audit(user, request, "OCI_INSTANCE_TERMINATED", "OCI_INSTANCE", instance_id, str(result))
    return result


@router.get("/accounts/{account_id}/instances/{instance_id}/console-connections")
def console_connections(
    account_id: int,
    instance_id: str,
    region: str,
    compartment_id: str,
    _: dict = Depends(current_user),
) -> list[dict]:
    require_instance_region(account_id, instance_id, region)
    try:
        return list_console_connections(account_id, instance_id, region, compartment_id)
    except Exception as exc:
        raise service_error(exc, "读取 VNC 连接失败") from exc


@router.post("/accounts/{account_id}/instances/{instance_id}/console-connections")
def create_console(
    account_id: int,
    instance_id: str,
    payload: ConsoleRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, payload.region)
    try:
        result = create_console_connection(
            account_id,
            instance_id,
            region=payload.region,
            public_key=payload.public_key,
        )
    except Exception as exc:
        raise service_error(exc, "创建 VNC 连接失败") from exc
    audit(user, request, "OCI_CONSOLE_CREATED", "OCI_INSTANCE", instance_id, result.get("id") or "")
    return result


@router.delete("/accounts/{account_id}/console-connections/{connection_id}")
def remove_console(
    account_id: int,
    connection_id: str,
    region: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        delete_console_connection(account_id, connection_id, region)
    except Exception as exc:
        raise service_error(exc, "删除 VNC 连接失败") from exc
    audit(user, request, "OCI_CONSOLE_DELETED", "OCI_CONSOLE", connection_id, region)
    return {"ok": True}










@router.put("/accounts/{account_id}/boot-volumes/{volume_id}")
def edit_boot_volume(
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
    update_cached_boot_volume(account_id, volume_id, result)
    audit(user, request, "OCI_BOOT_VOLUME_UPDATED", "OCI_BOOT_VOLUME", volume_id, payload.model_dump_json())
    return result


@router.post("/accounts/{account_id}/boot-volumes/{volume_id}/backups")
def backup_boot_volume(
    account_id: int,
    volume_id: str,
    payload: BootVolumeBackupRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_boot_volume(account_id, volume_id)
    try:
        result = create_boot_volume_backup(account_id, volume_id, payload.model_dump())
    except Exception as exc:
        raise service_error(exc, "创建引导卷备份失败") from exc
    audit(user, request, "OCI_BOOT_BACKUP_CREATED", "OCI_BOOT_VOLUME", volume_id, result.get("id") or "")
    return result


@router.get("/accounts/{account_id}/boot-volumes/{volume_id}/backups")
def boot_volume_backups(
    account_id: int,
    volume_id: str,
    region: str,
    compartment_id: str,
    _: dict = Depends(current_user),
) -> list[dict]:
    require_boot_volume(account_id, volume_id)
    try:
        return list_boot_volume_backups(
            account_id,
            volume_id,
            region=region,
            compartment_id=compartment_id,
        )
    except Exception as exc:
        raise service_error(exc, "读取引导卷备份失败") from exc


@router.delete("/accounts/{account_id}/boot-volume-backups/{backup_id}")
def remove_boot_volume_backup(
    account_id: int,
    backup_id: str,
    region: str,
    confirmation: str,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    if confirmation != "DELETE BACKUP":
        raise HTTPException(status_code=422, detail="请输入 DELETE BACKUP 确认删除备份")
    try:
        delete_boot_volume_backup(account_id, backup_id, region=region)
    except Exception as exc:
        raise service_error(exc, "删除引导卷备份失败") from exc
    audit(user, request, "OCI_BOOT_BACKUP_DELETED", "OCI_BOOT_BACKUP", backup_id, region)
    return {"ok": True}


@router.post("/accounts/{account_id}/instances/{instance_id}/images")
def backup_instance_image(
    account_id: int,
    instance_id: str,
    payload: InstanceImageRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, payload.region)
    try:
        result = create_instance_image(
            account_id,
            instance_id,
            region=payload.region,
            compartment_id=payload.compartment_id,
            display_name=payload.display_name,
        )
    except Exception as exc:
        raise service_error(exc, "创建实例镜像失败") from exc
    audit(user, request, "OCI_INSTANCE_IMAGE_CREATED", "OCI_INSTANCE", instance_id, result.get("id") or "")
    return result




























@router.post("/accounts/{account_id}/instances/{instance_id}/rescue")
def rescue_instance(
    account_id: int,
    instance_id: str,
    payload: RescueRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_instance_region(account_id, instance_id, payload.region)
    if payload.confirmation != "RESCUE":
        raise HTTPException(status_code=422, detail="请输入 RESCUE 确认进入救援流程")
    try:
        console = create_console_connection(
            account_id,
            instance_id,
            region=payload.region,
            public_key=payload.public_key,
        )
        action = instance_action(account_id, instance_id, "RESET", payload.region)
    except Exception as exc:
        raise service_error(exc, "进入救援模式失败") from exc
    update_cached_instance(
        account_id,
        instance_id,
        {"lifecycle_state": "REBOOTING"},
    )
    audit(user, request, "OCI_INSTANCE_RESCUE_STARTED", "OCI_INSTANCE", instance_id, console.get("id") or "")
    return {"ok": True, "console": console, "instance": action}












































