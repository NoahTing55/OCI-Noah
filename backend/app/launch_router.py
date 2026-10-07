from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from typing import Literal

from .database import add_audit_log, get_user_by_username
from .launch_repository import (
    delete_launch_job,
    get_launch_job,
    list_launch_attempts,
    list_launch_jobs,
)
from .launch_service import (
    ensure_launch_network,
    load_launch_catalog,
    load_launch_resources,
)
from .launch_task_service import (
    LaunchBusyError,
    cancel_launch_task,
    start_launch_task,
)
from .auth_session_service import validate_access_token
from .tenant_scope import require_account

router = APIRouter(prefix="/api/v1", tags=["OCI-N&T launch"])
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


def service_error(exc: Exception, prefix: str) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(
        status_code=400,
        detail=f"{prefix}：{type(exc).__name__}: {exc}",
    )


class LaunchJobCreateRequest(BaseModel):
    mode: str = "CREATE"
    catalog_token: str = Field(min_length=20, max_length=200)
    compartment_id: str = Field(min_length=20, max_length=255)
    availability_domain: str = Field(min_length=1, max_length=255)
    subnet_id: str = Field(min_length=20, max_length=255)
    image_id: str = Field(min_length=20, max_length=255)
    architecture: Literal["ARM", "AMD"]
    display_name: str = Field(default="N&T", min_length=1, max_length=255)
    ssh_public_key: str | None = Field(default=None, max_length=32768)
    login_mode: Literal["SSH_KEY", "ROOT_PASSWORD"] = "SSH_KEY"
    root_password: str | None = Field(default=None, max_length=128)
    assign_public_ip: bool = True
    assign_ipv6_ip: bool = False
    ocpus: float | None = Field(default=None, gt=0, le=160)
    memory_in_gbs: float | None = Field(default=None, gt=0, le=2048)
    boot_volume_size_in_gbs: int | None = Field(default=50, ge=50, le=32768)
    requested_count: int = Field(default=1, ge=1, le=20)
    # 0 means keep running until the target count succeeds or the user stops it.
    max_attempts: int = Field(default=1, ge=0, le=10000)
    retry_interval_seconds: int = Field(default=120, ge=15, le=86400)
    concurrency: int = Field(default=1, ge=1, le=5)


class LaunchNetworkEnsureRequest(BaseModel):
    catalog_token: str = Field(min_length=20, max_length=200)
    compartment_id: str = Field(min_length=20, max_length=255)
    availability_domain: str = Field(min_length=1, max_length=255)


@router.get("/accounts/{account_id}/launch/catalog")
def launch_catalog(
    account_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        result = load_launch_catalog(account_id)
    except Exception as exc:
        raise service_error(exc, "读取创建目录失败") from exc
    add_audit_log(
        user["username"],
        "OCI_LAUNCH_CATALOG_READ",
        client_ip(request),
        f"account={account_id},region={result['region']}",
        "OCI_ACCOUNT",
        str(account_id),
    )
    return result


@router.get("/accounts/{account_id}/launch/catalog/resources")
def launch_catalog_resources(
    account_id: int,
    request: Request,
    catalog_token: str = Query(min_length=20, max_length=200),
    compartment_id: str = Query(min_length=20, max_length=255),
    availability_domain: str = Query(min_length=1, max_length=255),
    architecture: Literal["ARM", "AMD"] = Query(),
    network_compartment_id: str | None = Query(default=None, min_length=20, max_length=255),
    preferred_subnet_id: str | None = Query(default=None, min_length=20, max_length=255),
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        result = load_launch_resources(
            account_id,
            catalog_token=catalog_token,
            compartment_id=compartment_id,
            availability_domain=availability_domain,
            architecture=architecture,
            network_compartment_id=network_compartment_id,
            preferred_subnet_id=preferred_subnet_id,
        )
    except Exception as exc:
        raise service_error(exc, "读取子网、镜像和配置失败") from exc
    add_audit_log(
        user["username"],
        "OCI_LAUNCH_RESOURCES_READ",
        client_ip(request),
        (
            f"account={account_id},compartment={compartment_id},"
            f"ad={availability_domain},architecture={architecture},"
            f"network_compartment={network_compartment_id or compartment_id}"
        ),
        "OCI_ACCOUNT",
        str(account_id),
    )
    return result


@router.post("/accounts/{account_id}/launch/network/ensure")
def ensure_launch_network_route(
    account_id: int,
    payload: LaunchNetworkEnsureRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        result = ensure_launch_network(
            account_id,
            catalog_token=payload.catalog_token,
            compartment_id=payload.compartment_id,
            availability_domain=payload.availability_domain,
        )
    except Exception as exc:
        raise service_error(exc, "自动创建 N&T 网络失败") from exc
    add_audit_log(
        user["username"],
        "OCI_LAUNCH_NETWORK_ENSURED",
        client_ip(request),
        (
            f"account={account_id},created={result.get('created')},"
            f"subnet={result.get('subnet', {}).get('id', '')}"
        ),
        "OCI_ACCOUNT",
        str(account_id),
    )
    return result


@router.post(
    "/accounts/{account_id}/launch/jobs",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_launch_job_route(
    account_id: int,
    payload: LaunchJobCreateRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    launch_request = payload.model_dump(
        exclude={
            "mode",
            "requested_count",
            "max_attempts",
            "retry_interval_seconds",
            "concurrency",
        }
    )
    try:
        return await start_launch_task(
            account_id=account_id,
            requested_by=user["username"],
            ip_address=client_ip(request),
            mode=payload.mode,
            request_payload=launch_request,
            requested_count=payload.requested_count,
            max_attempts=payload.max_attempts,
            retry_interval_seconds=payload.retry_interval_seconds,
            concurrency=payload.concurrency,
        )
    except LaunchBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise service_error(exc, "创建任务失败") from exc


@router.get("/accounts/{account_id}/launch/jobs")
def launch_jobs(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    _: dict = Depends(current_user),
) -> list[dict]:
    require_account(account_id)
    return list_launch_jobs(account_id, limit)


@router.get("/accounts/{account_id}/launch/jobs/{job_id}")
def launch_job_detail(
    account_id: int,
    job_id: int,
    _: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    job = get_launch_job(job_id)
    if not job or int(job["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="任务不存在或不属于当前租户")
    job["attempts"] = list_launch_attempts(job_id)
    return job


@router.post("/accounts/{account_id}/launch/jobs/{job_id}/cancel")
def cancel_launch_job_route(
    account_id: int,
    job_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    job = get_launch_job(job_id)
    if not job or int(job["account_id"]) != int(account_id):
        raise HTTPException(status_code=404, detail="任务不存在或不属于当前租户")
    if not cancel_launch_task(job_id, account_id):
        raise HTTPException(status_code=409, detail="任务已经结束，无法取消")
    add_audit_log(
        user["username"],
        "OCI_LAUNCH_CANCEL_REQUESTED",
        client_ip(request),
        f"job={job_id},account={account_id}",
        "OCI_LAUNCH_JOB",
        str(job_id),
    )
    return {"ok": True, "message": "已请求取消；正在进行的 OCI 请求结束后停止"}


@router.delete("/accounts/{account_id}/launch/jobs/{job_id}")
def delete_launch_job_route(
    account_id: int,
    job_id: int,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    require_account(account_id)
    try:
        result = delete_launch_job(job_id, account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit_log(
        user["username"],
        "OCI_LAUNCH_JOB_DELETED",
        client_ip(request),
        f"job={job_id},account={account_id},status={result['status']}",
        "OCI_LAUNCH_JOB",
        str(job_id),
    )
    return result
