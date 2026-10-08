import asyncio
import json
import hashlib
import sqlite3
import ipaddress
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import serialization
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from .account_repository import (
    create_account_record, delete_account_record, generate_unique_custom_name,
    find_account_by_credentials, get_account_proxy_settings, get_account_public,
    list_accounts_public, save_account_proxy, save_proxy_test_result,
    update_account_credentials, update_account_metadata,
)
from .account_check_service import execute_account_check
from .account_health_service import get_account_health_snapshot
from .account_check_schedule_service import (
    BulkCheckBusyError as ScheduledCheckBusyError,
    get_account_check_schedule,
    run_account_check_schedule_now,
    save_account_check_schedule,
    start_account_check_scheduler,
)
from .audit_presenter import present_audit_log
from .backup_service import (
    cleanup_old_backups, create_sqlite_backup, delete_sqlite_backup, list_backups,
    maintain_sqlite_database, verify_sqlite_backup,
)
from .backup_restore_service import list_restore_drills, run_backup_restore_drill
from .database_restore_service import list_database_restores, restore_database_backup
from .backup_schedule_service import (
    backup_scheduler_loop, mark_current_schedule_slot_handled, schedule_status,
)
from .cloudflare_repository import (
    create_cloudflare_account, delete_cloudflare_account, get_cloudflare_account,
    list_cloudflare_accounts, list_zones, update_cloudflare_account_check,
)
from .cloudflare_service import (
    CloudflareError, create_dns_record, delete_dns_record, list_dns_records,
    normalize_api_token, sync_zones, update_dns_record, verify_token,
    verify_zone_access,
)
from .config import settings
from . import adaptive_launch_scheduler as adaptive_scheduler
from .credential_crypto import decrypt_secret, encrypt_secret
from .database import (
    add_audit_log, ensure_admin_user, get_user_by_username, init_database,
    list_audit_logs, update_user_credentials, update_user_password,
)
from .feature_repository import touch_user_login
from .instance_batch_service import (
    InstanceBatchBusyError, start_instance_batch_task,
)
from .instance_cache_repository import (
    get_cached_instances,
    record_account_instance_sync_error,
    replace_account_instance_cache,
    update_cached_instance,
    update_cached_public_ip,
)
from .oci_config_parser import OciConfigParseError, parse_oci_api_config
from .oci_resource_service import (
    list_instances_all_regions,
    replace_ephemeral_public_ip,
)
from .oci_service import check_oci_credentials, instance_action
from .google_oauth_service import (
    GoogleOAuthError,
    build_authorization_url,
    consume_frontend_code,
    exchange_google_code,
    frontend_return_url,
    google_oauth_status,
    test_google_configuration,
)
from .proxy_service import (
    ProxyConfigurationError, ProxyRotationError, call_rotate_api, mask_proxy_url,
    normalize_proxy_url, normalize_rotation_config, test_proxy_url,
    test_proxy_url_details,
)
from .proxy_import import ProxyImportError, parse_proxy_import
from .proxy_repository import (
    ProxyAssignmentConflictError,
    auto_allocate_proxies,
    bind_account_proxy,
    create_proxy_profile,
    decrypt_proxy_profile_url,
    delete_proxy_profile,
    find_proxy_profile_by_url,
    get_proxy_profile,
    get_proxy_profile_with_secret,
    get_proxy_rotation_config,
    list_proxy_health_history,
    list_proxy_profiles,
    list_assignable_proxy_profiles,
    list_proxy_rotation_history,
    migrate_legacy_account_proxies,
    public_profile_for_edit,
    public_profile_with_display_url,
    proxy_allocation_preview,
    build_proxy_url_update,
    save_proxy_profile_rotate_result,
    save_proxy_profile_test_result,
    update_proxy_profile,
    unique_proxy_name,
)
from .region_names import get_region_display_name
from .security import verify_password
from .auth_session_service import (
    create_login_session, invalidate_user_sessions, list_user_sessions,
    cleanup_auth_history, login_ip_rate_status, recent_login_attempts,
    record_login_failure, revoke_other_sessions,
    revoke_session, token_session_id, user_lock_status, validate_access_token,
)
from .system_diagnostics import collect_support_bundle, collect_system_diagnostics
from .system_resource_service import (
    assert_task_resources, collect_resource_status, load_resource_limits,
    save_resource_limits,
)
from .system_monitor_service import (
    cleanup_history as cleanup_monitor_history, collect_system_info,
    current_monitor_snapshot, export_history_csv, history as monitor_history,
    list_alerts as list_monitor_alerts, list_network_interfaces,
    load_monitor_settings, monitor_scheduler_loop, reset_traffic_baseline,
    save_monitor_settings,
)
from .maintenance_state import status as maintenance_status
from .maintenance_route_guard import maintenance_active, protected_oci_write
from .task_recovery_service import preview_safe_resume, resume_task_safely
from .release_service import (
    current_release_info, list_release_history, record_application_start,
)
from .settings_repository import (
    get_bulk_check_interval_seconds,
    load_backup_policy,
    load_backup_schedule,
    load_telegram_settings,
    save_backup_policy,
    save_backup_schedule,
    save_bulk_check_interval_seconds,
    save_telegram_settings,
    load_google_auth_settings,
    save_google_auth_settings,
)
from .telegram_service import format_single_check_message, send_configured_telegram
from .task_repository import (
    cleanup_terminal_tasks, delete_terminal_task, get_current_task,
    get_task as get_manual_task, list_tasks,
)
from .task_service import (
    TaskBusyError,
    bulk_task_view,
    cancel_task,
    retry_failed_task,
    start_account_check_task,
    start_proxy_health_task,
)
from .tenant_scope import (
    require_instance as require_tenant_instance,
    require_private_ip as require_tenant_private_ip,
)
from .instance_router import router as instance_router
from .launch_router import router as launch_router
from .resource_router import router as resource_router
from .vnc_runtime import stop_all_vnc_sessions


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    username: str
    is_active: bool
    created_at: str


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class AccountResponse(BaseModel):
    id: int
    custom_name: str
    tenancy_ocid: str
    user_ocid: str
    fingerprint: str
    region: str
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
    registration_time: str | None = None
    survival_days: int | None = None
    proxy_enabled: bool = False
    proxy_profile_id: int | None = None
    proxy_profile_name: str | None = None
    has_proxy: bool = False
    proxy_label: str | None = None
    proxy_last_ip: str | None = None
    proxy_last_test_at: str | None = None
    proxy_last_error: str | None = None
    account_status: str
    last_error: str | None = None
    last_checked_at: str | None = None
    created_at: str
    updated_at: str


class AccountUpdateRequest(BaseModel):
    custom_name: str


class DeleteResponse(BaseModel):
    deleted: bool
    account_id: int


class BulkCheckResponse(BaseModel):
    total: int
    alive: int
    abnormal: int
    unknown: int
    accounts: list[AccountResponse]


class TelegramSettingsRequest(BaseModel):
    enabled: bool = False
    chat_id: str = ""
    bot_token: str | None = None
    account_check: bool = True
    instance_operation: bool = True
    launch_task: bool = True
    proxy_alert: bool = True
    system_backup: bool = True
    system_resource: bool = True


class TelegramSettingsResponse(BaseModel):
    enabled: bool
    chat_id: str
    has_bot_token: bool
    account_check: bool = True
    instance_operation: bool = True
    launch_task: bool = True
    proxy_alert: bool = True
    system_backup: bool = True
    system_resource: bool = True


class MessageResponse(BaseModel):
    ok: bool
    message: str


class TaskCleanupRequest(BaseModel):
    older_than_days: int = Field(default=30, ge=1, le=3650)
    keep_latest: int = Field(default=100, ge=0, le=1000)


class DatabaseMaintenanceRequest(BaseModel):
    vacuum: bool = False


class BackupPolicyRequest(BaseModel):
    retention_days: int = Field(default=14, ge=1, le=3650)
    keep_latest: int = Field(default=5, ge=1, le=1000)
    max_count: int = Field(default=50, ge=1, le=5000)


class BackupCleanupRequest(BaseModel):
    dry_run: bool = False


class BackupScheduleRequest(BaseModel):
    enabled: bool = False
    frequency: str = "DAILY"
    local_time: str = "03:00"
    weekday: int = Field(default=0, ge=0, le=6)
    timezone_offset_minutes: int = Field(default=480, ge=-720, le=840)


class DatabaseRestoreRequest(BaseModel):
    current_password: str
    confirmation: str


class TaskSafeResumeRequest(BaseModel):
    confirmation: str | None = None


class ResourceLimitsRequest(BaseModel):
    min_free_memory_mb: int = Field(default=384, ge=128, le=65536)
    min_free_disk_mb: int = Field(default=1024, ge=256, le=1048576)
    max_active_tasks: int = Field(default=2, ge=1, le=20)
    max_task_items: int = Field(default=100, ge=1, le=1000)


class MonitorSettingsRequest(BaseModel):
    enabled: bool = True
    interface: str = Field(default="auto", min_length=1, max_length=64)
    sample_interval_seconds: int = Field(default=60, ge=30, le=300)
    retention_days: int = Field(default=30, ge=1, le=365)
    timezone_offset_minutes: int = Field(default=480, ge=-720, le=840)
    include_all_containers: bool = False
    cpu_alert_percent: int = Field(default=90, ge=10, le=100)
    swap_alert_percent: int = Field(default=70, ge=10, le=100)
    monthly_traffic_quota_gb: int = Field(default=0, ge=0, le=1000000)
    alert_duration_minutes: int = Field(default=5, ge=1, le=1440)


class AccountProxyRequest(BaseModel):
    enabled: bool = False
    proxy_profile_id: int | None = None
    proxy_url: str | None = None
    proxy_label: str | None = None
    clear_proxy: bool = False


class AccountProxyResponse(BaseModel):
    account_id: int
    enabled: bool
    has_proxy: bool
    proxy_profile_id: int | None = None
    proxy_profile_name: str | None = None
    proxy_label: str | None = None
    display_url: str | None = None
    last_ip: str | None = None
    last_test_at: str | None = None
    last_error: str | None = None


class ProxyProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    proxy_url: str
    rotate_api_url: str | None = None
    rotate_api_method: str = "GET"
    rotate_api_headers: dict[str, str] | None = None
    rotate_api_body: str | None = None
    rotate_wait_seconds: int = Field(default=3, ge=0, le=60)


class ProxyProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    proxy_url: str | None = None
    scheme: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    clear_auth: bool = False
    rotate_api_url: str | None = None
    rotate_api_method: str | None = None
    rotate_api_headers: dict[str, str] | None = None
    rotate_api_body: str | None = None
    rotate_wait_seconds: int | None = Field(default=None, ge=0, le=60)
    clear_rotate_api: bool = False
    is_enabled: bool | None = None


class ProxyProfileResponse(BaseModel):
    id: int
    name: str
    scheme: str
    host: str
    port: int
    has_auth: bool
    has_rotate_api: bool = False
    rotate_api_method: str = "GET"
    rotate_wait_seconds: int = 3
    last_rotate_at: str | None = None
    last_rotate_error: str | None = None
    display_url: str | None = None
    last_ip: str | None = None
    last_test_at: str | None = None
    last_error: str | None = None
    last_latency_ms: int | None = None
    last_country_code: str | None = None
    last_country_name: str | None = None
    last_region_name: str | None = None
    last_city: str | None = None
    last_success_at: str | None = None
    consecutive_failures: int = 0
    is_enabled: bool = True
    paused_until: str | None = None
    assigned_count: int = 0
    assigned_account_id: int | None = None
    assigned_account_name: str | None = None
    created_at: str
    updated_at: str


class ProxyProfileDetailResponse(ProxyProfileResponse):
    username: str | None = None


class ProxyImportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=524288)
    default_scheme: str = "socks5"
    name_prefix: str = Field(default="代理", min_length=1, max_length=80)
    skip_existing: bool = True


class ProxyImportItemResult(BaseModel):
    line_number: int
    name: str
    status: str
    message: str
    profile_id: int | None = None


class ProxyImportResponse(BaseModel):
    total: int
    created: int
    skipped: int
    failed: int
    items: list[ProxyImportItemResult]


class ProxyRotateResponse(BaseModel):
    success: bool
    profile_id: int
    old_ip: str | None = None
    new_ip: str | None = None
    changed: bool = False
    message: str


class ProxyProfileDeleteResponse(BaseModel):
    deleted: bool
    profile_id: int
    detached_accounts: int


class AccountProxyTestResponse(BaseModel):
    success: bool
    ip_address: str
    message: str


class ProxyAllocationRequest(BaseModel):
    account_ids: list[int] | None = None
    reassign: bool = False


class ProxyUrlTestRequest(BaseModel):
    proxy_url: str


class InstanceActionRequest(BaseModel):
    action: str
    region: str | None = None


class PublicIpReplaceRequest(BaseModel):
    region: str | None = None


class InstanceBatchItemRequest(BaseModel):
    account_id: int = Field(ge=1)
    account_name: str | None = None
    instance_id: str | None = None
    display_name: str | None = None
    region: str | None = None
    private_ip_id: str | None = None


class InstanceBatchStartRequest(BaseModel):
    operation: str
    items: list[InstanceBatchItemRequest] = Field(min_length=1, max_length=100)
    interval_seconds: int = Field(default=2, ge=0, le=60)
    idempotency_key: str | None = Field(default=None, max_length=200)


class AccountImportPreviewResponse(BaseModel):
    tenancy_ocid: str
    user_ocid: str
    fingerprint: str
    computed_fingerprint: str
    fingerprint_matches: bool
    region: str
    home_region_name: str | None = None
    tenancy_name: str | None = None
    email: str | None = None
    account_type: str = "UNKNOWN"
    proxy_enabled: bool = False
    proxy_available: bool = True
    proxy_exit_ip: str | None = None
    duplicate: bool = False
    existing_account_id: int | None = None
    existing_account_name: str | None = None
    action: str
    validation_ok: bool = True
    validation_error: str | None = None


class CloudflareAccountCreateRequest(BaseModel):
    name: str
    api_token: str
    email: str | None = None


class CloudflareAccountResponse(BaseModel):
    id: int
    name: str
    email: str | None = None
    has_token: bool = True
    last_error: str | None = None
    last_checked_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DnsRecordRequest(BaseModel):
    type: str
    name: str
    content: str
    ttl: int = 1
    proxied: bool | None = None
    priority: int | None = None
    comment: str | None = None


class CredentialUpdateRequest(BaseModel):
    current_password: str
    username: str | None = None
    new_password: str | None = None


class CredentialUpdateResponse(BaseModel):
    ok: bool
    username: str
    access_token: str
    token_type: str = "bearer"


class BulkCheckStartRequest(BaseModel):
    interval_seconds: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)


class ProxyHealthStartRequest(BaseModel):
    profile_ids: list[int] | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)


class BulkCheckSettingsRequest(BaseModel):
    interval_seconds: int


class GoogleCodeExchangeRequest(BaseModel):
    code: str


class GoogleAuthSettingsRequest(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str | None = None
    redirect_uri: str = ""
    frontend_return_url: str = "/"
    allowed_emails: list[str] = Field(default_factory=list)


class GoogleAuthSettingsResponse(BaseModel):
    enabled: bool
    configured: bool
    client_id: str
    has_client_secret: bool
    redirect_uri: str
    frontend_return_url: str
    allowed_emails: list[str]
    source: str


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    ensure_admin_user(settings.admin_username, settings.admin_password)
    migrate_legacy_account_proxies()
    record_application_start()
    backup_stop_event = asyncio.Event()
    monitor_stop_event = asyncio.Event()
    background_tasks = []
    if not settings.candidate_mode:
        background_tasks.append(asyncio.create_task(backup_scheduler_loop(backup_stop_event)))
        background_tasks.append(asyncio.create_task(monitor_scheduler_loop(monitor_stop_event)))
    # OCI has no background scheduler. Background work is limited to local SQLite
    # backup and local host metrics; every OCI request remains user-triggered.
    try:
        cleanup_auth_history()
        start_account_check_scheduler()
        yield
    finally:
        backup_stop_event.set()
        monitor_stop_event.set()
        for background_task in background_tasks:
            try:
                await asyncio.wait_for(background_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                background_task.cancel()
        await stop_all_vnc_sessions()


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.middleware("http")
async def maintenance_request_guard(request: Request, call_next):
    state = maintenance_status()
    if (
        state.get("active")
        and request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and not request.url.path.endswith("/health")
    ):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "系统正在执行数据库维护，请稍后重试",
                "maintenance": state,
            },
        )
    if protected_oci_write(request.method, request.url.path):
        try:
            if maintenance_active(settings.db_path):
                return JSONResponse(
                    status_code=503,
                    content={"detail": "系统处于发布维护模式，暂停新的 OCI 操作"},
                )
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            return JSONResponse(
                status_code=503,
                content={"detail": "无法确认发布维护状态，已安全阻止 OCI 操作"},
            )
    response = await call_next(request)
    if request.url.path.startswith(f"{settings.api_prefix}/auth/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


app.include_router(instance_router)
app.include_router(launch_router)
app.include_router(resource_router)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix}/auth/login")


def client_ip(request: Request) -> str | None:
    candidates: list[str] = []

    connecting_ip = request.headers.get("CF-Connecting-IP")
    forwarded_for = request.headers.get("X-Forwarded-For")
    real_ip = request.headers.get("X-Real-IP")

    if connecting_ip:
        candidates.append(connecting_ip)
    if forwarded_for:
        candidates.extend(forwarded_for.split(","))
    if real_ip:
        candidates.append(real_ip)
    if request.client:
        candidates.append(request.client.host)

    saw_local_address = False

    for candidate in candidates:
        value = candidate.strip()
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError:
            continue

        if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
            saw_local_address = True
            continue

        return parsed.compressed

    return "LOCAL" if saw_local_address else None


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    validated = validate_access_token(token)
    if not validated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录状态无效、已注销或已经过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user, session = validated
    result = dict(user)
    result["_session_id"] = session.get("session_id") if session else token_session_id(token)
    return result


def validate_account_fields(custom_name: str, tenancy_ocid: str, user_ocid: str, fingerprint: str, region: str) -> tuple[str, str, str, str, str]:
    custom_name = custom_name.strip()
    tenancy_ocid = tenancy_ocid.strip()
    user_ocid = user_ocid.strip()
    fingerprint = fingerprint.strip().lower()
    region = region.strip().lower()
    if not custom_name:
        raise HTTPException(status_code=422, detail="账户名称不能为空")
    if len(custom_name) > 120:
        raise HTTPException(status_code=422, detail="账户名称不能超过 120 个字符")
    if not tenancy_ocid.startswith("ocid1.tenancy."):
        raise HTTPException(status_code=422, detail="Tenancy OCID 格式不正确")
    if not user_ocid.startswith("ocid1.user."):
        raise HTTPException(status_code=422, detail="User OCID 格式不正确")
    if not fingerprint or len(fingerprint) > 100:
        raise HTTPException(status_code=422, detail="API Key Fingerprint 格式不正确")
    if not region or " " in region or len(region) > 64:
        raise HTTPException(status_code=422, detail="OCI 区域格式不正确")
    return custom_name, tenancy_ocid, user_ocid, fingerprint, region


def compute_private_key_fingerprint(
    private_key_pem: str,
    passphrase: str | None = None,
) -> str:
    password = passphrase.encode("utf-8") if passphrase else None
    try:
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"),
            password=password,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="OCI 私钥无法解析，或私钥密码不正确",
        ) from exc
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashlib.md5(public_der, usedforsecurity=False).hexdigest()
    return ":".join(digest[index:index + 2] for index in range(0, len(digest), 2))


async def parse_import_credentials(
    *,
    api_config: str,
    private_key_passphrase: str | None,
    private_key_file: UploadFile | None,
) -> dict:
    try:
        parsed = parse_oci_api_config(api_config)
    except OciConfigParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _, tenancy_ocid, user_ocid, fingerprint, region = validate_account_fields(
        "AUTO",
        parsed.tenancy_ocid,
        parsed.user_ocid,
        parsed.fingerprint,
        parsed.region,
    )
    uploaded_private_key = None
    if private_key_file is not None and private_key_file.filename:
        key_content = await private_key_file.read(131073)
        if len(key_content) > 131072:
            raise HTTPException(status_code=413, detail="私钥文件过大")
        try:
            uploaded_private_key = key_content.decode("utf-8").strip() + "\n"
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="私钥必须是 UTF-8 PEM 文本") from exc
    private_key_pem = uploaded_private_key or parsed.private_key_pem
    if not private_key_pem:
        detail = "未找到 OCI API 私钥。请上传 PEM 私钥文件，或者把 PEM 私钥内容粘贴在整段配置下面。"
        if parsed.key_file_hint:
            detail += f"配置中的 key_file 路径仅是提示，浏览器无法读取该路径：{parsed.key_file_hint}"
        raise HTTPException(status_code=422, detail=detail)
    if "-----BEGIN" not in private_key_pem or "PRIVATE KEY-----" not in private_key_pem:
        raise HTTPException(status_code=422, detail="无法识别 PEM 私钥内容")
    passphrase = (
        private_key_passphrase.strip() if private_key_passphrase else None
    ) or parsed.private_key_passphrase
    computed_fingerprint = compute_private_key_fingerprint(private_key_pem, passphrase)
    return {
        "tenancy_ocid": tenancy_ocid,
        "user_ocid": user_ocid,
        "fingerprint": fingerprint,
        "region": region,
        "private_key_pem": private_key_pem,
        "passphrase": passphrase,
        "computed_fingerprint": computed_fingerprint,
        "fingerprint_matches": computed_fingerprint.lower() == fingerprint.lower(),
    }


def resolve_import_proxy(
    *,
    proxy_enabled: bool,
    proxy_profile_id: int | None,
    proxy_url: str | None,
) -> tuple[str | None, dict | None]:
    if proxy_profile_id is not None and proxy_url and proxy_url.strip():
        raise HTTPException(status_code=422, detail="已保存代理与新增代理不能同时提交")
    selected_proxy_profile = None
    normalized_proxy_url = None
    if proxy_profile_id is not None:
        selected_proxy_profile = get_proxy_profile(proxy_profile_id)
        if not selected_proxy_profile:
            raise HTTPException(status_code=404, detail="选择的代理不存在或已被删除")
        try:
            normalized_proxy_url = decrypt_proxy_profile_url(proxy_profile_id)
        except InvalidToken as exc:
            raise HTTPException(status_code=500, detail="所选代理凭据无法解密") from exc
    elif proxy_url and proxy_url.strip():
        try:
            normalized_proxy_url = normalize_proxy_url(proxy_url)
        except ProxyConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if proxy_enabled and not normalized_proxy_url:
        raise HTTPException(status_code=422, detail="启用租户独立代理时必须选择已保存代理或新增代理")
    return normalized_proxy_url, selected_proxy_profile


def require_account(account_id: int) -> dict:
    account = get_account_public(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="OCI 账户不存在")
    return account


def check_result_error(result) -> str:
    return result.error or "未知 OCI 验证错误"


def build_proxy_response(account_id: int) -> AccountProxyResponse:
    record = get_account_proxy_settings(account_id)
    if not record:
        raise HTTPException(status_code=404, detail="OCI 账户不存在")
    display_url = None
    if record["proxy_url_encrypted"]:
        try:
            display_url = mask_proxy_url(decrypt_secret(record["proxy_url_encrypted"]))
        except InvalidToken:
            display_url = "代理凭据无法解密"
    return AccountProxyResponse(
        account_id=account_id,
        enabled=bool(record["proxy_enabled"]),
        has_proxy=bool(record["proxy_url_encrypted"]),
        proxy_profile_id=record.get("proxy_profile_id"),
        proxy_profile_name=record.get("proxy_profile_name"),
        proxy_label=record.get("proxy_label"),
        display_url=display_url,
        last_ip=record.get("proxy_last_ip"),
        last_test_at=record.get("proxy_last_test_at"),
        last_error=record.get("proxy_last_error"),
    )


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "version": settings.version, "status": "running"}


@app.get(f"{settings.api_prefix}/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, version=settings.version, timestamp=datetime.now(timezone.utc).isoformat())


@app.post(f"{settings.api_prefix}/auth/login", response_model=TokenResponse)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    ip_address = client_ip(request)
    ip_rate = login_ip_rate_status(ip_address)
    if ip_rate.get("limited"):
        add_audit_log(
            form.username[:120], "LOGIN_IP_RATE_LIMITED", ip_address,
            f"failed_count={ip_rate.get('failed_count')}",
        )
        raise HTTPException(
            status_code=429,
            detail="当前来源登录尝试过多，请 15 分钟后重试",
            headers={"Retry-After": "900"},
        )
    user = get_user_by_username(form.username)
    user_agent = request.headers.get("User-Agent")
    lock = user_lock_status(form.username)
    if lock.get("locked"):
        add_audit_log(form.username, "LOGIN_BLOCKED", ip_address, f"locked_until={lock.get('locked_until')}")
        raise HTTPException(status_code=429, detail=f"登录失败次数过多，账户已临时锁定至 {lock.get('locked_until')}")
    if not user or not user["is_active"] or not verify_password(form.password, user["password_hash"]):
        failure = record_login_failure(form.username, ip_address, user_agent, "INVALID_CREDENTIALS")
        add_audit_log(form.username, "LOGIN_FAILED", ip_address, f"failed_count={failure.get('failed_login_count')}")
        detail = "用户名或密码错误"
        if failure.get("locked"):
            detail = f"登录失败次数过多，账户已临时锁定至 {failure.get('locked_until')}"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})
    token, session = create_login_session(
        username=user["username"], ip_address=ip_address, user_agent=user_agent, login_method="LOCAL"
    )
    add_audit_log(user["username"], "LOGIN_SUCCESS", ip_address, f"session={session.get('session_id')}")
    touch_user_login(user["username"])
    return TokenResponse(access_token=token)


@app.get(f"{settings.api_prefix}/auth/me", response_model=UserResponse)
def current_user(user: dict = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=user["id"], username=user["username"], is_active=bool(user["is_active"]), created_at=user["created_at"])


@app.post(f"{settings.api_prefix}/auth/change-password", response_model=MessageResponse)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    if payload.new_password == "":
        raise HTTPException(status_code=422, detail="新密码不能为空")
    stored = get_user_by_username(user["username"])
    if not stored or not verify_password(
        payload.current_password, stored["password_hash"]
    ):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    update_user_password(user["username"], payload.new_password)
    invalidate_user_sessions(int(user["id"]), reason="PASSWORD_CHANGED", increment_token_version=True)
    add_audit_log(
        user["username"],
        "PASSWORD_CHANGED",
        client_ip(request),
        resource_type="USER",
        resource_id=str(user["id"]),
    )
    return MessageResponse(ok=True, message="密码已修改")


@app.put(
    f"{settings.api_prefix}/settings/credentials",
    response_model=CredentialUpdateResponse,
)
def update_login_credentials(
    payload: CredentialUpdateRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> CredentialUpdateResponse:
    stored = get_user_by_username(user["username"])
    if not stored or not verify_password(
        payload.current_password, stored["password_hash"]
    ):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.username is None and payload.new_password is None:
        raise HTTPException(status_code=422, detail="请填写新用户名或新密码")
    try:
        updated = update_user_credentials(
            user["username"],
            new_username=payload.username,
            new_password=payload.new_password,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    add_audit_log(
        updated["username"],
        "LOGIN_CREDENTIALS_UPDATED",
        client_ip(request),
        f"old_username={user['username']},new_username={updated['username']}",
        "USER",
        str(updated["id"]),
    )
    invalidate_user_sessions(int(updated["id"]), reason="CREDENTIALS_UPDATED", increment_token_version=True)
    token, _session = create_login_session(
        username=updated["username"],
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        login_method="LOCAL",
    )
    return CredentialUpdateResponse(ok=True, username=updated["username"], access_token=token)


@app.post(f"{settings.api_prefix}/auth/logout", response_model=MessageResponse)
def logout(request: Request, user: dict = Depends(get_current_user)) -> MessageResponse:
    session_id = user.get("_session_id")
    if session_id:
        revoke_session(session_id, int(user["id"]), reason="USER_LOGOUT")
    add_audit_log(user["username"], "LOGOUT", client_ip(request), resource_type="AUTH_SESSION", resource_id=session_id)
    return MessageResponse(ok=True, message="当前设备已退出登录")


@app.get(f"{settings.api_prefix}/auth/sessions")
def auth_sessions(user: dict = Depends(get_current_user)) -> list[dict]:
    return list_user_sessions(int(user["id"]), current_session_id=user.get("_session_id"))


@app.delete(f"{settings.api_prefix}/auth/sessions/{{session_id}}", response_model=MessageResponse)
def revoke_auth_session(session_id: str, request: Request, user: dict = Depends(get_current_user)) -> MessageResponse:
    if not revoke_session(session_id, int(user["id"]), reason="USER_REVOKED"):
        raise HTTPException(status_code=404, detail="会话不存在或已经注销")
    add_audit_log(user["username"], "AUTH_SESSION_REVOKED", client_ip(request), resource_type="AUTH_SESSION", resource_id=session_id)
    return MessageResponse(ok=True, message="会话已注销")


@app.post(f"{settings.api_prefix}/auth/sessions/logout-others", response_model=MessageResponse)
def logout_other_sessions(request: Request, user: dict = Depends(get_current_user)) -> MessageResponse:
    count = revoke_other_sessions(int(user["id"]), user.get("_session_id"))
    add_audit_log(user["username"], "AUTH_OTHER_SESSIONS_REVOKED", client_ip(request), f"count={count}", "AUTH_SESSION")
    return MessageResponse(ok=True, message=f"已注销其他 {count} 个会话")


@app.get(f"{settings.api_prefix}/auth/login-attempts")
def login_attempt_history(limit: int = 100, _: dict = Depends(get_current_user)) -> list[dict]:
    return recent_login_attempts(limit)


@app.get(f"{settings.api_prefix}/auth/google/status")
def google_login_status() -> dict:
    return google_oauth_status()


@app.get(f"{settings.api_prefix}/auth/google/start")
def google_login_start() -> RedirectResponse:
    try:
        return RedirectResponse(build_authorization_url(), status_code=302)
    except GoogleOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/auth/google/callback")
def google_login_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    if error:
        message = error_description or error
        add_audit_log(None, "GOOGLE_LOGIN_FAILED", client_ip(request), message)
        return RedirectResponse(frontend_return_url(error=message), status_code=302)
    if not code or not state:
        message = "Google 回调缺少 code 或 state"
        add_audit_log(None, "GOOGLE_LOGIN_FAILED", client_ip(request), message)
        return RedirectResponse(frontend_return_url(error=message), status_code=302)
    try:
        one_time_code = exchange_google_code(code, state)
    except GoogleOAuthError as exc:
        add_audit_log(None, "GOOGLE_LOGIN_FAILED", client_ip(request), str(exc))
        return RedirectResponse(frontend_return_url(error=str(exc)), status_code=302)
    return RedirectResponse(frontend_return_url(code=one_time_code), status_code=302)


@app.post(
    f"{settings.api_prefix}/auth/google/exchange",
    response_model=TokenResponse,
)
def google_login_exchange(
    payload: GoogleCodeExchangeRequest,
    request: Request,
) -> TokenResponse:
    username = consume_frontend_code(payload.code)
    if not username:
        raise HTTPException(status_code=401, detail="Google 登录码无效或已过期")
    user = get_user_by_username(username)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="本地管理员账户不可用")
    add_audit_log(
        user["username"],
        "GOOGLE_LOGIN_SUCCESS",
        client_ip(request),
        resource_type="USER",
        resource_id=str(user["id"]),
    )
    token, _session = create_login_session(
        username=user["username"],
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        login_method="GOOGLE",
    )
    touch_user_login(user["username"])
    return TokenResponse(access_token=token)


@app.get(
    f"{settings.api_prefix}/settings/google-auth",
    response_model=GoogleAuthSettingsResponse,
)
def get_google_auth_settings(_: dict = Depends(get_current_user)) -> GoogleAuthSettingsResponse:
    return GoogleAuthSettingsResponse(**load_google_auth_settings(include_secret=False))


@app.put(
    f"{settings.api_prefix}/settings/google-auth",
    response_model=GoogleAuthSettingsResponse,
)
def update_google_auth_settings(
    payload: GoogleAuthSettingsRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> GoogleAuthSettingsResponse:
    try:
        saved = save_google_auth_settings(
            enabled=payload.enabled,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            redirect_uri=payload.redirect_uri,
            frontend_return_url=payload.frontend_return_url,
            allowed_emails=payload.allowed_emails,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    add_audit_log(
        user["username"], "GOOGLE_AUTH_SETTINGS_UPDATED", client_ip(request),
        f"enabled={saved['enabled']},allowed={len(saved['allowed_emails'])}",
        "SYSTEM_SETTING", "google-auth",
    )
    return GoogleAuthSettingsResponse(**saved)


@app.post(f"{settings.api_prefix}/settings/google-auth/test", response_model=MessageResponse)
def test_google_auth_settings(
    request: Request,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    try:
        result = test_google_configuration()
    except GoogleOAuthError as exc:
        add_audit_log(user["username"], "GOOGLE_AUTH_TEST_FAILED", client_ip(request), str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    add_audit_log(user["username"], "GOOGLE_AUTH_TEST_SUCCESS", client_ip(request))
    return MessageResponse(ok=True, message=result["message"])


@app.get(f"{settings.api_prefix}/accounts", response_model=list[AccountResponse])
def list_accounts(_: dict = Depends(get_current_user)) -> list[AccountResponse]:
    return [AccountResponse(**account) for account in list_accounts_public()]


@app.post(
    f"{settings.api_prefix}/accounts/import-preview",
    response_model=AccountImportPreviewResponse,
)
async def preview_account_import(
    request: Request,
    api_config: str = Form(...),
    private_key_passphrase: str | None = Form(None),
    private_key_file: UploadFile | None = File(None),
    proxy_enabled: bool = Form(False),
    proxy_profile_id: int | None = Form(None),
    proxy_url: str | None = Form(None),
    _: dict = Depends(get_current_user),
) -> AccountImportPreviewResponse:
    credentials = await parse_import_credentials(
        api_config=api_config,
        private_key_passphrase=private_key_passphrase,
        private_key_file=private_key_file,
    )
    duplicate = find_account_by_credentials(
        credentials["tenancy_ocid"],
        credentials["user_ocid"],
        credentials["fingerprint"],
    )
    normalized_proxy_url, _selected = resolve_import_proxy(
        proxy_enabled=proxy_enabled,
        proxy_profile_id=proxy_profile_id,
        proxy_url=proxy_url,
    )
    base = {
        "tenancy_ocid": credentials["tenancy_ocid"],
        "user_ocid": credentials["user_ocid"],
        "fingerprint": credentials["fingerprint"],
        "computed_fingerprint": credentials["computed_fingerprint"],
        "fingerprint_matches": credentials["fingerprint_matches"],
        "region": credentials["region"],
        "proxy_enabled": bool(proxy_enabled),
        "proxy_available": True,
        "duplicate": bool(duplicate),
        "existing_account_id": duplicate.get("id") if duplicate else None,
        "existing_account_name": duplicate.get("custom_name") if duplicate else None,
        "action": "UPDATE" if duplicate else "CREATE",
    }
    if not credentials["fingerprint_matches"]:
        add_audit_log(
            _["username"],
            "OCI_ACCOUNT_IMPORT_PREVIEW_FAILED",
            client_ip(request),
            "API Key Fingerprint 与私钥不匹配",
            "OCI_ACCOUNT",
        )
        return AccountImportPreviewResponse(
            **base,
            validation_ok=False,
            validation_error="API Key Fingerprint 与上传私钥不匹配",
        )
    proxy_exit_ip = None
    if proxy_enabled and normalized_proxy_url:
        try:
            proxy_exit_ip = test_proxy_url(normalized_proxy_url)
        except Exception as exc:
            error = f"代理测试失败：{type(exc).__name__}: {exc}"
            add_audit_log(
                _["username"],
                "OCI_ACCOUNT_IMPORT_PREVIEW_FAILED",
                client_ip(request),
                error,
                "OCI_ACCOUNT",
            )
            return AccountImportPreviewResponse(
                **base,
                proxy_available=False,
                validation_ok=False,
                validation_error=error,
            )
    result = check_oci_credentials(
        tenancy_ocid=credentials["tenancy_ocid"],
        user_ocid=credentials["user_ocid"],
        fingerprint=credentials["fingerprint"],
        region=credentials["region"],
        private_key_pem=credentials["private_key_pem"],
        private_key_passphrase=credentials["passphrase"],
        proxy_url=normalized_proxy_url if proxy_enabled else None,
    )
    validation_ok = result.status == "ALIVE"
    validation_error = None if validation_ok else check_result_error(result)
    add_audit_log(
        _["username"],
        "OCI_ACCOUNT_IMPORT_PREVIEWED" if validation_ok else "OCI_ACCOUNT_IMPORT_PREVIEW_FAILED",
        client_ip(request),
        f"action={base['action']}; status={result.status}; duplicate={bool(duplicate)}",
        "OCI_ACCOUNT",
        str(duplicate.get("id")) if duplicate else None,
    )
    return AccountImportPreviewResponse(
        **base,
        home_region_name=result.home_region_name,
        tenancy_name=result.tenancy_name,
        email=result.email,
        account_type=result.account_type,
        proxy_exit_ip=proxy_exit_ip,
        validation_ok=validation_ok,
        validation_error=validation_error,
    )


@app.post(f"{settings.api_prefix}/accounts", response_model=AccountResponse)
async def create_account(
    request: Request,
    api_config: str = Form(...),
    private_key_passphrase: str | None = Form(None),
    private_key_file: UploadFile | None = File(None),
    proxy_enabled: bool = Form(False),
    proxy_profile_id: int | None = Form(None),
    proxy_url: str | None = Form(None),
    proxy_label: str | None = Form(None),
    update_existing: bool = Form(False),
    user: dict = Depends(get_current_user),
) -> AccountResponse:
    credentials = await parse_import_credentials(
        api_config=api_config,
        private_key_passphrase=private_key_passphrase,
        private_key_file=private_key_file,
    )
    if not credentials["fingerprint_matches"]:
        raise HTTPException(
            status_code=422,
            detail=(
                "API Key Fingerprint 与上传私钥不匹配："
                f"配置为 {credentials['fingerprint']}，私钥计算结果为 "
                f"{credentials['computed_fingerprint']}"
            ),
        )
    normalized_proxy_url, selected_proxy_profile = resolve_import_proxy(
        proxy_enabled=proxy_enabled,
        proxy_profile_id=proxy_profile_id,
        proxy_url=proxy_url,
    )
    proxy_exit_ip = None
    clean_proxy_label = proxy_label.strip() if proxy_label else None
    if proxy_enabled and normalized_proxy_url:
        try:
            proxy_exit_ip = test_proxy_url(normalized_proxy_url)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"租户独立代理测试失败：{type(exc).__name__}: {exc}",
            ) from exc

    result = check_oci_credentials(
        tenancy_ocid=credentials["tenancy_ocid"],
        user_ocid=credentials["user_ocid"],
        fingerprint=credentials["fingerprint"],
        region=credentials["region"],
        private_key_pem=credentials["private_key_pem"],
        private_key_passphrase=credentials["passphrase"],
        proxy_url=normalized_proxy_url if proxy_enabled else None,
    )
    checked_at = datetime.now(timezone.utc).isoformat()
    if result.status != "ALIVE":
        add_audit_log(
            user["username"],
            "OCI_ACCOUNT_IMPORT_FAILED",
            client_ip(request),
            check_result_error(result),
            "OCI_ACCOUNT",
        )
        raise HTTPException(status_code=400, detail="OCI 验证失败：" + check_result_error(result))

    duplicate = find_account_by_credentials(
        credentials["tenancy_ocid"],
        credentials["user_ocid"],
        credentials["fingerprint"],
    )
    if duplicate and not update_existing:
        raise HTTPException(
            status_code=409,
            detail=f"该 OCI API 已存在：{duplicate['custom_name']}。请确认预览后选择更新已有账户。",
        )
    if selected_proxy_profile is not None:
        owner_id = selected_proxy_profile.get("assigned_account_id")
        if owner_id is not None and int(owner_id) != int(duplicate["id"] if duplicate else 0):
            raise HTTPException(
                status_code=409,
                detail=f"该代理已分配给租户“{selected_proxy_profile.get('assigned_account_name') or owner_id}”，请选择未分配代理",
            )

    values = {
        "tenancy_ocid": credentials["tenancy_ocid"],
        "user_ocid": credentials["user_ocid"],
        "fingerprint": credentials["fingerprint"],
        "region": credentials["region"],
        "private_key_encrypted": encrypt_secret(credentials["private_key_pem"]),
        "passphrase_encrypted": encrypt_secret(credentials["passphrase"]) if credentials["passphrase"] else None,
        "email": result.email,
        "user_name": result.user_name,
        "user_lifecycle_state": result.user_lifecycle_state,
        "user_time_created": result.user_time_created,
        "tenancy_name": result.tenancy_name,
        "home_region_key": result.home_region_key,
        "home_region_name": result.home_region_name,
        "account_type": result.account_type,
        "subscription_plan_type": result.subscription_plan_type,
        "subscription_upgrade_state": result.subscription_upgrade_state,
        "subscription_time_start": result.subscription_time_start,
        "account_status": result.status,
        "last_error": result.error,
        "last_checked_at": checked_at,
    }

    if duplicate:
        account = update_account_credentials(int(duplicate["id"]), **values)
        if not account:
            raise HTTPException(status_code=404, detail="需要更新的 OCI 账户不存在")
        custom_name = account["custom_name"]
        audit_action = "OCI_ACCOUNT_CREDENTIALS_UPDATED"
    else:
        region_base_name = get_region_display_name(
            result.home_region_name,
            result.home_region_key,
            configured_region=credentials["region"],
        )
        custom_name = generate_unique_custom_name(region_base_name)
        try:
            account = create_account_record(custom_name=custom_name, **values)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="该 OCI API 凭据已经存在") from exc
        audit_action = "OCI_ACCOUNT_CREATED"

    if selected_proxy_profile is not None:
        try:
            bind_account_proxy(
                account_id=account["id"],
                profile_id=selected_proxy_profile["id"],
                enabled=proxy_enabled,
            )
        except (ProxyAssignmentConflictError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=409, detail=str(exc) or "代理刚刚被其他租户占用") from exc
        if proxy_exit_ip:
            save_proxy_profile_test_result(
                selected_proxy_profile["id"],
                ip_address=proxy_exit_ip,
                tested_at=checked_at,
                error=None,
            )
    elif normalized_proxy_url:
        profile = find_proxy_profile_by_url(normalized_proxy_url)
        if not profile:
            profile_name = unique_proxy_name(clean_proxy_label or f"{custom_name}代理")
            profile = create_proxy_profile(name=profile_name, proxy_url=normalized_proxy_url)
        try:
            bind_account_proxy(
                account_id=account["id"],
                profile_id=profile["id"],
                enabled=proxy_enabled,
            )
        except (ProxyAssignmentConflictError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=409, detail=str(exc) or "代理刚刚被其他租户占用") from exc
        if proxy_exit_ip:
            save_proxy_profile_test_result(
                profile["id"],
                ip_address=proxy_exit_ip,
                tested_at=checked_at,
                error=None,
            )
    elif duplicate:
        bind_account_proxy(
            account_id=account["id"],
            profile_id=account.get("proxy_profile_id"),
            enabled=False,
        )

    refreshed = get_account_public(account["id"])
    if refreshed:
        account = refreshed
    add_audit_log(
        user["username"],
        audit_action,
        client_ip(request),
        f"{custom_name}; proxy={'enabled' if proxy_enabled else 'disabled'}",
        "OCI_ACCOUNT",
        str(account["id"]),
    )
    return AccountResponse(**account)


@app.get(f"{settings.api_prefix}/accounts/{{account_id}}", response_model=AccountResponse)
def get_account(account_id: int, _: dict = Depends(get_current_user)) -> AccountResponse:
    return AccountResponse(**require_account(account_id))


@app.put(f"{settings.api_prefix}/accounts/{{account_id}}", response_model=AccountResponse)
def update_account(
    account_id: int,
    payload: AccountUpdateRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> AccountResponse:
    account = require_account(account_id)
    custom_name = payload.custom_name.strip()
    if not custom_name:
        raise HTTPException(status_code=422, detail="自定义名称不能为空")
    if len(custom_name) > 120:
        raise HTTPException(status_code=422, detail="自定义名称不能超过 120 个字符")
    updated = update_account_metadata(account_id, custom_name=custom_name)
    if not updated:
        raise HTTPException(status_code=404, detail="OCI 账户不存在")
    add_audit_log(
        user["username"], "OCI_ACCOUNT_RENAMED", client_ip(request),
        f"{account['custom_name']} -> {custom_name}", "OCI_ACCOUNT", str(account_id),
    )
    return AccountResponse(**updated)


# OCI-N&T V1.0.4 1.0.5-alert-health-taskcenter1 account health route
@app.get(f"{settings.api_prefix}/account-health")
def account_health_snapshot(_: dict = Depends(get_current_user)) -> dict:
    return get_account_health_snapshot()


@app.post(f"{settings.api_prefix}/accounts/{{account_id}}/check", response_model=AccountResponse)
def check_account(account_id: int, request: Request, user: dict = Depends(get_current_user)) -> AccountResponse:
    try:
        updated = execute_account_check(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    add_audit_log(user["username"], "OCI_ACCOUNT_CHECKED", client_ip(request), updated["account_status"], "OCI_ACCOUNT", str(account_id))
    send_configured_telegram(format_single_check_message(updated), "account_check")
    return AccountResponse(**updated)


@app.post(f"{settings.api_prefix}/bulk/account-checks", status_code=202)
async def check_all_accounts(
    payload: BulkCheckStartRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    interval_seconds = (
        payload.interval_seconds
        if payload.interval_seconds is not None
        else get_bulk_check_interval_seconds(settings.bulk_check_interval_seconds)
    )
    try:
        task = await start_account_check_task(
            requested_by=user["username"],
            ip_address=client_ip(request),
            interval_seconds=interval_seconds,
            idempotency_key=payload.idempotency_key,
        )
        return bulk_task_view(task)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/bulk/account-checks/current")
def current_bulk_check(_: dict = Depends(get_current_user)) -> dict | None:
    task = get_current_task("ACCOUNT_CHECK")
    return bulk_task_view(get_manual_task(task["id"]) if task else None)


@app.get(f"{settings.api_prefix}/bulk/account-checks/history")
def bulk_check_history(
    limit: int = 20,
    _: dict = Depends(get_current_user),
) -> list[dict]:
    result = []
    for task in list_tasks(limit=limit, task_type="ACCOUNT_CHECK"):
        detailed = get_manual_task(task["id"])
        if detailed:
            result.append(bulk_task_view(detailed))
    return result


@app.get(f"{settings.api_prefix}/bulk/account-checks/{{job_id}}")
def bulk_check_detail(
    job_id: int,
    _: dict = Depends(get_current_user),
) -> dict:
    task = get_manual_task(job_id)
    if not task or task.get("task_type") != "ACCOUNT_CHECK":
        raise HTTPException(status_code=404, detail="全部检测任务不存在")
    return bulk_task_view(task)


@app.post(f"{settings.api_prefix}/bulk/account-checks/{{job_id}}/cancel")
def cancel_bulk_check_job(
    job_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    if not cancel_task(job_id):
        task = get_manual_task(job_id)
        if not task or task.get("task_type") != "ACCOUNT_CHECK":
            raise HTTPException(status_code=404, detail="全部检测任务不存在")
        raise HTTPException(status_code=409, detail="该任务已经结束，无法取消")
    add_audit_log(
        user["username"],
        "OCI_ACCOUNTS_BULK_CHECK_CANCEL_REQUESTED",
        client_ip(request),
        f"task={job_id}",
        "MANUAL_TASK",
        str(job_id),
    )
    return {"ok": True, "message": "已请求取消，当前账户检测结束后停止"}


@app.post(f"{settings.api_prefix}/tasks/proxy-health", status_code=202)
async def start_proxy_health_check(
    payload: ProxyHealthStartRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        return await start_proxy_health_task(
            requested_by=user["username"],
            ip_address=client_ip(request),
            profile_ids=payload.profile_ids,
            idempotency_key=payload.idempotency_key,
        )
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(f"{settings.api_prefix}/tasks/instance-batch", status_code=202)
async def start_instance_batch(
    payload: InstanceBatchStartRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        return await start_instance_batch_task(
            requested_by=user["username"],
            ip_address=client_ip(request),
            operation=payload.operation,
            items=[item.model_dump() for item in payload.items],
            interval_seconds=payload.interval_seconds,
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InstanceBatchBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/tasks")
def task_center_list(
    limit: int = 50,
    task_type: str | None = None,
    task_status: str | None = None,
    _: dict = Depends(get_current_user),
) -> list[dict]:
    return list_tasks(limit=limit, task_type=task_type, status=task_status)


@app.get(f"{settings.api_prefix}/tasks/current")
def task_center_current(
    task_type: str | None = None,
    _: dict = Depends(get_current_user),
) -> dict | None:
    task = get_current_task(task_type)
    return get_manual_task(task["id"]) if task else None


@app.get(f"{settings.api_prefix}/tasks/{{task_id}}")
def task_center_detail(
    task_id: int,
    _: dict = Depends(get_current_user),
) -> dict:
    task = get_manual_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.post(f"{settings.api_prefix}/tasks/{{task_id}}/cancel")
def task_center_cancel(
    task_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    if not cancel_task(task_id):
        task = get_manual_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        raise HTTPException(status_code=409, detail="该任务已经结束，无法取消")
    add_audit_log(
        user["username"], "MANUAL_TASK_CANCEL_REQUESTED", client_ip(request),
        f"task={task_id}", "MANUAL_TASK", str(task_id),
    )
    return {"ok": True, "message": "已请求取消任务"}


@app.post(f"{settings.api_prefix}/tasks/{{task_id}}/retry", status_code=202)
async def task_center_retry(
    task_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        return await retry_failed_task(
            task_id=task_id,
            requested_by=user["username"],
            ip_address=client_ip(request),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@app.get(f"{settings.api_prefix}/tasks/{{task_id}}/safe-resume-preview")
async def task_safe_resume_preview(
    task_id: int,
    _: dict = Depends(get_current_user),
) -> dict:
    try:
        return await preview_safe_resume(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(f"{settings.api_prefix}/tasks/{{task_id}}/safe-resume", status_code=202)
async def task_safe_resume(
    task_id: int,
    payload: TaskSafeResumeRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        result = await resume_task_safely(
            task_id,
            requested_by=user["username"],
            ip_address=client_ip(request),
            confirmation=payload.confirmation,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError, TaskBusyError, InstanceBatchBusyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit_log(
        user["username"], "MANUAL_TASK_SAFE_RESUMED", client_ip(request),
        f"original_task={task_id},resumed={result.get('resumed')}",
        "MANUAL_TASK", str(task_id),
    )
    return result


@app.delete(f"{settings.api_prefix}/tasks/{{task_id}}", response_model=MessageResponse)
def task_center_delete(
    task_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    try:
        task = delete_terminal_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    add_audit_log(
        user["username"], "MANUAL_TASK_DELETED", client_ip(request),
        f"task={task_id},status={task['status']}", "MANUAL_TASK", str(task_id),
    )
    return MessageResponse(ok=True, message=f"任务 #{task_id} 已删除")


@app.post(f"{settings.api_prefix}/tasks/cleanup")
def task_center_cleanup(
    payload: TaskCleanupRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    result = cleanup_terminal_tasks(
        older_than_days=payload.older_than_days,
        keep_latest=payload.keep_latest,
    )
    add_audit_log(
        user["username"], "MANUAL_TASK_HISTORY_CLEANED", client_ip(request),
        f"deleted={result['deleted']},days={result['older_than_days']},keep={result['keep_latest']}",
        "MANUAL_TASK",
    )
    return result


def proxy_rotation_from_create(payload: ProxyProfileCreateRequest) -> dict | None:
    api_url = str(payload.rotate_api_url or "").strip()
    if not api_url:
        return None
    return normalize_rotation_config(
        api_url=api_url,
        method=payload.rotate_api_method,
        headers=payload.rotate_api_headers,
        body=payload.rotate_api_body,
        wait_seconds=payload.rotate_wait_seconds,
    )


def proxy_rotation_from_update(
    profile_id: int, payload: ProxyProfileUpdateRequest
) -> tuple[bool, dict | None]:
    fields = set(payload.model_fields_set)
    rotation_fields = {
        "rotate_api_url", "rotate_api_method", "rotate_api_headers",
        "rotate_api_body", "rotate_wait_seconds", "clear_rotate_api",
    }
    if not fields.intersection(rotation_fields):
        return False, None
    if payload.clear_rotate_api:
        return True, None
    try:
        current = get_proxy_rotation_config(profile_id) or {}
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="更换 IP API 凭据无法解密") from exc
    api_url = (
        str(payload.rotate_api_url or "").strip()
        if "rotate_api_url" in fields and str(payload.rotate_api_url or "").strip()
        else current.get("api_url", "")
    )
    if not api_url:
        raise HTTPException(status_code=422, detail="请填写更换 IP API 地址，或勾选清除 API")
    return True, normalize_rotation_config(
        api_url=api_url,
        method=(
            payload.rotate_api_method
            if "rotate_api_method" in fields and payload.rotate_api_method
            else current.get("method", "GET")
        ),
        headers=(
            payload.rotate_api_headers
            if "rotate_api_headers" in fields
            else current.get("headers", {})
        ),
        body=(
            payload.rotate_api_body
            if "rotate_api_body" in fields
            else current.get("body")
        ),
        wait_seconds=(
            payload.rotate_wait_seconds
            if "rotate_wait_seconds" in fields and payload.rotate_wait_seconds is not None
            else current.get("wait_seconds", 3)
        ),
    )


@app.post(f"{settings.api_prefix}/proxy/test", response_model=AccountProxyTestResponse)
def test_unsaved_proxy(
    payload: ProxyUrlTestRequest,
    _: dict = Depends(get_current_user),
) -> AccountProxyTestResponse:
    try:
        normalized = normalize_proxy_url(payload.proxy_url)
        ip_address = test_proxy_url(normalized)
    except (ProxyConfigurationError, ProxyAssignmentConflictError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"代理测试失败：{type(exc).__name__}: {exc}") from exc
    return AccountProxyTestResponse(
        success=True,
        ip_address=ip_address,
        message=f"代理连接成功，出口 IP：{ip_address}",
    )


@app.get(f"{settings.api_prefix}/proxies", response_model=list[ProxyProfileResponse])
def get_proxy_profiles(_: dict = Depends(get_current_user)) -> list[ProxyProfileResponse]:
    profiles: list[ProxyProfileResponse] = []
    for item in list_proxy_profiles():
        profile = public_profile_with_display_url(item["id"]) or item
        profiles.append(ProxyProfileResponse(**profile))
    return profiles


@app.get(f"{settings.api_prefix}/accounts/{{account_id}}/available-proxies", response_model=list[ProxyProfileResponse])
def get_available_proxies_for_account(
    account_id: int,
    _: dict = Depends(get_current_user),
) -> list[ProxyProfileResponse]:
    try:
        items = list_assignable_proxy_profiles(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    profiles: list[ProxyProfileResponse] = []
    for item in items:
        public = public_profile_with_display_url(int(item["id"])) or item
        profiles.append(ProxyProfileResponse(**public))
    return profiles


@app.post(f"{settings.api_prefix}/proxy-allocation/preview")
def preview_proxy_allocation(
    payload: ProxyAllocationRequest,
    _: dict = Depends(get_current_user),
) -> dict:
    return proxy_allocation_preview(payload.account_ids, reassign=payload.reassign)


@app.post(f"{settings.api_prefix}/proxy-allocation/execute")
def execute_proxy_allocation(
    payload: ProxyAllocationRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        result = auto_allocate_proxies(payload.account_ids, reassign=payload.reassign)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="代理分配发生并发冲突，请刷新后重试") from exc
    add_audit_log(
        user["username"], "PROXY_AUTO_ALLOCATED", client_ip(request),
        f"total={result['total']};assigned={result['assigned']};unassigned={result['unassigned']};reassign={int(payload.reassign)}",
        "PROXY_PROFILE", None,
    )
    return result


@app.post(
    f"{settings.api_prefix}/proxies",
    response_model=ProxyProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_proxy_profile(
    payload: ProxyProfileCreateRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> ProxyProfileResponse:
    try:
        profile = create_proxy_profile(
            name=payload.name,
            proxy_url=payload.proxy_url,
            rotation_config=proxy_rotation_from_create(payload),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="代理名称已经存在") from exc
    except (ProxyConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    add_audit_log(
        user["username"], "PROXY_PROFILE_CREATED", client_ip(request),
        f"name={profile['name']};rotate_api={int(profile.get('has_rotate_api', False))}",
        "PROXY_PROFILE", str(profile["id"]),
    )
    public = public_profile_with_display_url(profile["id"]) or profile
    return ProxyProfileResponse(**public)


@app.post(
    f"{settings.api_prefix}/proxies/import",
    response_model=ProxyImportResponse,
)
def import_proxy_profiles(
    payload: ProxyImportRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> ProxyImportResponse:
    try:
        parsed = parse_proxy_import(
            payload.text,
            default_scheme=payload.default_scheme,
            name_prefix=payload.name_prefix,
        )
    except (ProxyImportError, ProxyConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    created = 0
    skipped = 0
    failed = 0
    results: list[ProxyImportItemResult] = []
    for item in parsed:
        try:
            existing = find_proxy_profile_by_url(item.proxy_url)
            if existing and payload.skip_existing:
                skipped += 1
                results.append(ProxyImportItemResult(
                    line_number=item.line_number,
                    name=item.name,
                    status="SKIPPED",
                    message=f"已存在：{existing['name']}",
                    profile_id=int(existing["id"]),
                ))
                continue
            profile_name = unique_proxy_name(item.name)
            rotation_config = None
            if item.rotate_api_url:
                rotation_config = normalize_rotation_config(
                    api_url=item.rotate_api_url,
                    method=item.rotate_api_method,
                    headers=item.rotate_api_headers,
                    body=item.rotate_api_body,
                    wait_seconds=item.rotate_wait_seconds,
                )
            profile = create_proxy_profile(
                name=profile_name,
                proxy_url=item.proxy_url,
                rotation_config=rotation_config,
            )
            created += 1
            results.append(ProxyImportItemResult(
                line_number=item.line_number,
                name=profile["name"],
                status="CREATED",
                message="已导入",
                profile_id=int(profile["id"]),
            ))
        except Exception as exc:
            failed += 1
            results.append(ProxyImportItemResult(
                line_number=item.line_number,
                name=item.name,
                status="FAILED",
                message=str(exc),
            ))

    add_audit_log(
        user["username"], "PROXY_PROFILES_IMPORTED", client_ip(request),
        f"total={len(parsed)};created={created};skipped={skipped};failed={failed}",
        "PROXY_PROFILE", None,
    )
    return ProxyImportResponse(
        total=len(parsed), created=created, skipped=skipped, failed=failed, items=results
    )


@app.get(
    f"{settings.api_prefix}/proxies/{{profile_id}}",
    response_model=ProxyProfileDetailResponse,
)
def get_proxy_profile_detail(
    profile_id: int,
    _: dict = Depends(get_current_user),
) -> ProxyProfileDetailResponse:
    profile = public_profile_for_edit(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="代理不存在")
    return ProxyProfileDetailResponse(**profile)


@app.put(
    f"{settings.api_prefix}/proxies/{{profile_id}}",
    response_model=ProxyProfileResponse,
)
def edit_proxy_profile(
    profile_id: int,
    payload: ProxyProfileUpdateRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> ProxyProfileResponse:
    if not payload.model_fields_set:
        raise HTTPException(status_code=422, detail="没有需要保存的修改")
    try:
        fields = set(payload.model_fields_set)
        structured_proxy_fields = {"scheme", "host", "port", "username", "password", "clear_auth"}
        if payload.proxy_url is not None and fields.intersection(structured_proxy_fields):
            raise ProxyConfigurationError("代理地址和分项代理字段不能同时提交")
        proxy_url = payload.proxy_url
        if fields.intersection(structured_proxy_fields):
            proxy_url = build_proxy_url_update(
                profile_id,
                {name: getattr(payload, name) for name in structured_proxy_fields if name in fields},
            )
        update_rotation, rotation_config = proxy_rotation_from_update(profile_id, payload)
        update_kwargs = {"name": payload.name, "proxy_url": proxy_url}
        if update_rotation:
            update_kwargs["rotation_config"] = rotation_config
        if "is_enabled" in fields:
            update_kwargs["is_enabled"] = bool(payload.is_enabled)
        profile = update_proxy_profile(profile_id, **update_kwargs)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="代理名称已经存在") from exc
    except (ProxyConfigurationError, ProxyAssignmentConflictError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not profile:
        raise HTTPException(status_code=404, detail="代理不存在")
    add_audit_log(
        user["username"], "PROXY_PROFILE_UPDATED", client_ip(request),
        f"name={profile['name']}", "PROXY_PROFILE", str(profile_id),
    )
    public = public_profile_with_display_url(profile_id) or profile
    return ProxyProfileResponse(**public)


@app.delete(
    f"{settings.api_prefix}/proxies/{{profile_id}}",
    response_model=ProxyProfileDeleteResponse,
)
def remove_proxy_profile(
    profile_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
) -> ProxyProfileDeleteResponse:
    deleted, detached = delete_proxy_profile(profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="代理不存在")
    add_audit_log(
        user["username"], "PROXY_PROFILE_DELETED", client_ip(request),
        f"detached_accounts={detached}", "PROXY_PROFILE", str(profile_id),
    )
    return ProxyProfileDeleteResponse(
        deleted=True, profile_id=profile_id, detached_accounts=detached
    )


@app.post(
    f"{settings.api_prefix}/proxies/{{profile_id}}/test",
    response_model=AccountProxyTestResponse,
)
def test_saved_proxy_profile(
    profile_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
) -> AccountProxyTestResponse:
    if not get_proxy_profile(profile_id):
        raise HTTPException(status_code=404, detail="代理不存在")
    try:
        proxy_url = decrypt_proxy_profile_url(profile_id)
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="代理凭据无法解密") from exc
    tested_at = datetime.now(timezone.utc).isoformat()
    try:
        details = test_proxy_url_details(proxy_url)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        save_proxy_profile_test_result(
            profile_id, ip_address=None, tested_at=tested_at, error=error
        )
        raise HTTPException(status_code=400, detail=f"代理测试失败：{error}") from exc
    ip_address = details["ip_address"]
    save_proxy_profile_test_result(
        profile_id,
        ip_address=ip_address,
        tested_at=tested_at,
        error=None,
        latency_ms=details.get("latency_ms"),
        country_code=details.get("country_code"),
        country_name=details.get("country_name"),
        region_name=details.get("region_name"),
        city=details.get("city"),
    )
    add_audit_log(
        user["username"], "PROXY_PROFILE_TESTED", client_ip(request),
        f"ip={ip_address};latency_ms={details.get('latency_ms') or ''}",
        "PROXY_PROFILE", str(profile_id),
    )
    location = " / ".join(
        value for value in [details.get("country_name"), details.get("region_name"), details.get("city")]
        if value
    )
    suffix = f" · {location}" if location else ""
    return AccountProxyTestResponse(
        success=True,
        ip_address=ip_address,
        message=f"代理连接成功，出口 IP：{ip_address} · {details.get('latency_ms')}ms{suffix}",
    )


@app.post(
    f"{settings.api_prefix}/proxies/{{profile_id}}/rotate",
    response_model=ProxyRotateResponse,
)
def rotate_saved_proxy_profile(
    profile_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
) -> ProxyRotateResponse:
    profile = get_proxy_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="代理不存在")
    if not profile.get("has_rotate_api"):
        raise HTTPException(status_code=409, detail="该代理尚未配置更换 IP API")
    try:
        rotation = get_proxy_rotation_config(profile_id)
        proxy_url = decrypt_proxy_profile_url(profile_id)
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="代理或更换 IP API 凭据无法解密") from exc
    if not rotation:
        raise HTTPException(status_code=409, detail="该代理尚未配置更换 IP API")

    old_ip = profile.get("last_ip")
    rotated_at = datetime.now(timezone.utc).isoformat()
    try:
        status_code = call_rotate_api(rotation)
        wait_seconds = int(rotation.get("wait_seconds") or 0)
        if wait_seconds:
            time.sleep(wait_seconds)
        new_ip = test_proxy_url(proxy_url)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        save_proxy_profile_rotate_result(
            profile_id, rotated_at=rotated_at, error=error, old_ip=old_ip
        )
        raise HTTPException(status_code=400, detail=f"更换 IP 失败：{error}") from exc

    tested_at = datetime.now(timezone.utc).isoformat()
    changed = bool(old_ip and new_ip and old_ip != new_ip)
    save_proxy_profile_rotate_result(
        profile_id, rotated_at=rotated_at, error=None, old_ip=old_ip,
        new_ip=new_ip, changed=changed, http_status=status_code,
    )
    save_proxy_profile_test_result(
        profile_id, ip_address=new_ip, tested_at=tested_at, error=None
    )
    message = (
        f"更换 IP 成功：{old_ip} → {new_ip}"
        if changed
        else f"更换 IP API 已返回 HTTP {status_code}，当前出口 IP：{new_ip}"
    )
    add_audit_log(
        user["username"], "PROXY_PROFILE_IP_ROTATED", client_ip(request),
        f"changed={int(changed)};old_ip={old_ip or ''};new_ip={new_ip}",
        "PROXY_PROFILE", str(profile_id),
    )
    return ProxyRotateResponse(
        success=True, profile_id=profile_id, old_ip=old_ip, new_ip=new_ip,
        changed=changed, message=message,
    )


@app.get(f"{settings.api_prefix}/proxies/{{profile_id}}/health-history")
def proxy_health_history(
    profile_id: int,
    limit: int = 30,
    _: dict = Depends(get_current_user),
) -> list[dict]:
    if not get_proxy_profile(profile_id):
        raise HTTPException(status_code=404, detail="代理不存在")
    return list_proxy_health_history(profile_id, limit)


@app.get(f"{settings.api_prefix}/proxies/{{profile_id}}/rotation-history")
def proxy_rotation_history(
    profile_id: int,
    limit: int = 30,
    _: dict = Depends(get_current_user),
) -> list[dict]:
    if not get_proxy_profile(profile_id):
        raise HTTPException(status_code=404, detail="代理不存在")
    return list_proxy_rotation_history(profile_id, limit)


@app.get(f"{settings.api_prefix}/accounts/{{account_id}}/proxy", response_model=AccountProxyResponse)
def get_account_proxy(account_id: int, _: dict = Depends(get_current_user)) -> AccountProxyResponse:
    return build_proxy_response(account_id)


@app.put(f"{settings.api_prefix}/accounts/{{account_id}}/proxy", response_model=AccountProxyResponse)
def update_account_proxy(
    account_id: int,
    payload: AccountProxyRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> AccountProxyResponse:
    existing = get_account_proxy_settings(account_id)
    if not existing:
        raise HTTPException(status_code=404, detail="OCI 账户不存在")

    if payload.clear_proxy:
        save_account_proxy(
            account_id=account_id,
            enabled=False,
            proxy_label=None,
            clear_proxy=True,
        )
    elif payload.proxy_profile_id is not None:
        if not get_proxy_profile(payload.proxy_profile_id):
            raise HTTPException(status_code=404, detail="选择的代理不存在")
        try:
            bind_account_proxy(
                account_id=account_id,
                profile_id=payload.proxy_profile_id,
                enabled=payload.enabled,
            )
        except ProxyAssignmentConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="该代理刚刚被其他租户占用，请重新选择") from exc
    elif payload.proxy_url:
        try:
            normalized = normalize_proxy_url(payload.proxy_url)
            name = unique_proxy_name(
                (payload.proxy_label or "").strip() or f"账户 {account_id} 代理"
            )
            profile = create_proxy_profile(name=name, proxy_url=normalized)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="代理名称已经存在") from exc
        except (ProxyConfigurationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            bind_account_proxy(
                account_id=account_id,
                profile_id=profile["id"],
                enabled=payload.enabled,
            )
        except (ProxyAssignmentConflictError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=409, detail="新代理绑定失败，请刷新后重试") from exc
    else:
        current_profile_id = existing.get("proxy_profile_id")
        if payload.enabled and current_profile_id is None and not existing.get("proxy_url_encrypted"):
            raise HTTPException(status_code=422, detail="启用代理时必须选择或新增代理")
        if current_profile_id is not None:
            try:
                bind_account_proxy(
                    account_id=account_id,
                    profile_id=int(current_profile_id),
                    enabled=payload.enabled,
                )
            except ProxyAssignmentConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        else:
            save_account_proxy(
                account_id=account_id,
                enabled=payload.enabled,
                proxy_label=existing.get("proxy_label"),
            )

    result = build_proxy_response(account_id)
    add_audit_log(
        user["username"], "OCI_ACCOUNT_PROXY_UPDATED", client_ip(request),
        f"enabled={result.enabled},profile_id={result.proxy_profile_id or ''},name={result.proxy_profile_name or ''}",
        "OCI_ACCOUNT", str(account_id),
    )
    return result


@app.post(f"{settings.api_prefix}/accounts/{{account_id}}/proxy/test", response_model=AccountProxyTestResponse)
def test_account_proxy(
    account_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
) -> AccountProxyTestResponse:
    record = get_account_proxy_settings(account_id)
    if not record:
        raise HTTPException(status_code=404, detail="OCI 账户不存在")
    if not record["proxy_url_encrypted"]:
        raise HTTPException(status_code=422, detail="该账户尚未选择代理")
    try:
        proxy_url = decrypt_secret(record["proxy_url_encrypted"])
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="账户代理无法解密") from exc
    tested_at = datetime.now(timezone.utc).isoformat()
    try:
        ip_address = test_proxy_url(proxy_url)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        save_proxy_test_result(account_id, None, tested_at, error)
        raise HTTPException(status_code=400, detail=f"代理测试失败：{error}") from exc
    save_proxy_test_result(account_id, ip_address, tested_at, None)
    add_audit_log(
        user["username"], "OCI_ACCOUNT_PROXY_TESTED", client_ip(request),
        f"ip={ip_address}", "OCI_ACCOUNT", str(account_id),
    )
    return AccountProxyTestResponse(
        success=True,
        ip_address=ip_address,
        message=f"代理连接成功，出口 IP：{ip_address}",
    )


@app.get(f"{settings.api_prefix}/accounts/{{account_id}}/instances")
def get_instances(
    account_id: int,
    _: dict = Depends(get_current_user),
) -> dict:
    require_account(account_id)
    return get_cached_instances(account_id)


@app.get(f"{settings.api_prefix}/instances")
def get_all_instances(
    account_id: int | None = None,
    _: dict = Depends(get_current_user),
) -> dict:
    if account_id is not None:
        require_account(account_id)
    return get_cached_instances(account_id)


def sync_instance_cache(account_id: int) -> dict:
    accounts = [require_account(account_id)]
    for account in accounts:
        try:
            result = list_instances_all_regions(account["id"])
        except Exception as exc:
            record_account_instance_sync_error(
                account_id=account["id"],
                account_name=account["custom_name"],
                error=f"{type(exc).__name__}: {exc}",
            )
            continue

        for instance in result["instances"]:
            instance["account_id"] = account["id"]
            instance["account_name"] = account["custom_name"]
            instance["account_email"] = account.get("email")
        for error in result["errors"]:
            error["account_name"] = account["custom_name"]

        replace_account_instance_cache(
            account_id=account["id"],
            account_name=account["custom_name"],
            account_email=account.get("email"),
            instances=result["instances"],
            errors=result["errors"],
            regions_scanned=result["regions_scanned"],
        )

    return get_cached_instances(account_id)


@app.post(f"{settings.api_prefix}/instances/sync")
def sync_all_instances(
    request: Request,
    account_id: int | None = None,
    user: dict = Depends(get_current_user),
) -> dict:
    if account_id is None:
        raise HTTPException(
            status_code=422,
            detail="v7 只允许同步当前租户，请提供 account_id",
        )
    result = sync_instance_cache(account_id)
    add_audit_log(
        user["username"],
        "OCI_INSTANCES_SYNCED",
        client_ip(request),
        (
            f"account_id={account_id},"
            f"instances={len(result['instances'])},"
            f"errors={len(result['errors'])}"
        ),
        "OCI_INSTANCE",
        str(account_id),
    )
    return result


@app.post(f"{settings.api_prefix}/accounts/{{account_id}}/instances/sync")
def sync_account_instances(
    account_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    result = sync_instance_cache(account_id)
    add_audit_log(
        user["username"],
        "OCI_INSTANCES_SYNCED",
        client_ip(request),
        (
            f"account_id={account_id},"
            f"instances={len(result['instances'])},"
            f"errors={len(result['errors'])}"
        ),
        "OCI_INSTANCE",
        str(account_id),
    )
    return result


@app.post(f"{settings.api_prefix}/accounts/{{account_id}}/instances/{{instance_id}}/actions")
def run_instance_action(account_id: int, instance_id: str, payload: InstanceActionRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    require_tenant_instance(account_id, instance_id)
    try:
        result = instance_action(account_id, instance_id, payload.action, payload.region)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"实例操作失败：{type(exc).__name__}: {exc}") from exc
    lifecycle_state = {
        "START": "STARTING",
        "STOP": "STOPPING",
        "SOFTSTOP": "STOPPING",
        "RESET": "REBOOTING",
        "SOFTRESET": "REBOOTING",
    }.get(payload.action.upper())
    if lifecycle_state:
        update_cached_instance(
            account_id,
            instance_id,
            {"lifecycle_state": lifecycle_state},
        )
    add_audit_log(user["username"], "OCI_INSTANCE_ACTION", client_ip(request), f"{payload.action}:{instance_id}", "OCI_INSTANCE", instance_id)
    return result


@app.post(
    f"{settings.api_prefix}/accounts/{{account_id}}/public-ips/"
    "{private_ip_id}/replace"
)
def replace_public_ip(
    account_id: int,
    private_ip_id: str,
    payload: PublicIpReplaceRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    require_tenant_private_ip(account_id, private_ip_id)
    try:
        result = replace_ephemeral_public_ip(
            account_id,
            private_ip_id,
            payload.region,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"更换临时公网 IP 失败：{type(exc).__name__}: {exc}",
        ) from exc
    add_audit_log(
        user["username"],
        "OCI_PUBLIC_IP_REPLACED",
        client_ip(request),
        f"old={result.get('old_ip') or 'none'},new={result.get('new_ip') or 'pending'}",
        "OCI_PUBLIC_IP",
        result.get("public_ip_id"),
    )
    update_cached_public_ip(
        account_id,
        private_ip_id,
        result.get("new_ip"),
    )
    return result




@app.delete(f"{settings.api_prefix}/accounts/{{account_id}}", response_model=DeleteResponse)
def delete_account(account_id: int, request: Request, user: dict = Depends(get_current_user)) -> DeleteResponse:
    account = require_account(account_id)
    deleted = delete_account_record(account_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="OCI 账户不存在")
    add_audit_log(user["username"], "OCI_ACCOUNT_DELETED", client_ip(request), account["custom_name"], "OCI_ACCOUNT", str(account_id))
    return DeleteResponse(deleted=True, account_id=account_id)

@app.get(f"{settings.api_prefix}/settings/telegram", response_model=TelegramSettingsResponse)
def get_telegram_settings(_: dict = Depends(get_current_user)) -> TelegramSettingsResponse:
    return TelegramSettingsResponse(**load_telegram_settings(include_token=False))


@app.put(f"{settings.api_prefix}/settings/telegram", response_model=TelegramSettingsResponse)
def update_telegram_settings(payload: TelegramSettingsRequest, request: Request, user: dict = Depends(get_current_user)) -> TelegramSettingsResponse:
    chat_id = payload.chat_id.strip()
    existing = load_telegram_settings(include_token=False)
    has_token = bool(payload.bot_token and payload.bot_token.strip()) or existing["has_bot_token"]
    if payload.enabled and not chat_id:
        raise HTTPException(status_code=422, detail="启用 Telegram 通知时必须填写 Chat ID")
    if payload.enabled and not has_token:
        raise HTTPException(status_code=422, detail="启用 Telegram 通知时必须填写 Bot Token")
    saved = save_telegram_settings(
        payload.enabled,
        chat_id,
        payload.bot_token,
        account_check=payload.account_check,
        instance_operation=payload.instance_operation,
        launch_task=payload.launch_task,
        proxy_alert=payload.proxy_alert,
        system_backup=payload.system_backup,
        system_resource=payload.system_resource,
    )
    add_audit_log(
        user["username"],
        "TELEGRAM_SETTINGS_UPDATED",
        client_ip(request),
        (
            f"enabled={saved['enabled']},account={saved['account_check']},"
            f"instance={saved['instance_operation']},launch={saved['launch_task']},"
            f"proxy={saved['proxy_alert']},backup={saved['system_backup']},resource={saved['system_resource']}"
        ),
        "SYSTEM_SETTING",
    )
    return TelegramSettingsResponse(**saved)


@app.post(f"{settings.api_prefix}/settings/telegram/test", response_model=MessageResponse)
def test_telegram_settings(request: Request, user: dict = Depends(get_current_user)) -> MessageResponse:
    sent, message = send_configured_telegram(f"OCI-N&T Telegram 测试成功\n\n系统版本：{settings.display_version}\n通知连接工作正常。")
    add_audit_log(user["username"], "TELEGRAM_TEST_SENT", client_ip(request), message, "SYSTEM_SETTING")
    if not sent:
        raise HTTPException(status_code=400, detail=message)
    return MessageResponse(ok=True, message=message)


# OCI-N&T V1.0.4 1.0.4-api-scheduled-check3 routes
@app.get(f"{settings.api_prefix}/settings/account-check-schedule")
def account_check_schedule_settings(_: dict = Depends(get_current_user)) -> dict:
    return get_account_check_schedule()


@app.put(f"{settings.api_prefix}/settings/account-check-schedule")
def update_account_check_schedule_settings(
    payload: dict, request: Request, user: dict = Depends(get_current_user)
) -> dict:
    try:
        return save_account_check_schedule(
            payload, changed_by=user["username"], ip_address=client_ip(request)
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(f"{settings.api_prefix}/settings/account-check-schedule/run-now", status_code=202)
async def run_account_check_schedule_now_route(
    request: Request, user: dict = Depends(get_current_user)
) -> dict:
    try:
        return await run_account_check_schedule_now(
            requested_by=user["username"], ip_address=client_ip(request)
        )
    except ScheduledCheckBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/settings/account-check")
def get_account_check_settings(
    _: dict = Depends(get_current_user),
) -> dict:
    return {
        "background_queries": False,
        "interval_seconds": get_bulk_check_interval_seconds(
            settings.bulk_check_interval_seconds
        ),
        "minimum_interval_seconds": 0,
    }


@app.put(f"{settings.api_prefix}/settings/account-check")
def update_account_check_settings(
    payload: BulkCheckSettingsRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        value = save_bulk_check_interval_seconds(payload.interval_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    add_audit_log(
        user["username"],
        "BULK_CHECK_INTERVAL_UPDATED",
        client_ip(request),
        f"interval_seconds={value}",
        "SYSTEM_SETTING",
    )
    return {
        "background_queries": False,
        "interval_seconds": value,
        "minimum_interval_seconds": 0,
    }


# Compatibility endpoint for older frontends. Automatic OCI monitoring is
# permanently disabled in v7. This endpoint only reports the current policy.
@app.get(f"{settings.api_prefix}/settings/monitor")
def legacy_monitor_settings(
    _: dict = Depends(get_current_user),
) -> dict:
    return {
        "enabled": False,
        "running": bool((get_current_job() or {}).get("is_active")),
        "background_queries": False,
        "interval_seconds": get_bulk_check_interval_seconds(
            settings.bulk_check_interval_seconds
        ),
        "message": "v7 不执行后台 OCI 查询；仅支持用户主动检测",
    }


@app.put(f"{settings.api_prefix}/settings/monitor")
def reject_legacy_monitor_update(
    _: dict,
    __: dict = Depends(get_current_user),
) -> dict:
    raise HTTPException(
        status_code=410,
        detail="v7 已移除后台 OCI 自动查询，请使用手动全部检测设置",
    )


@app.post(f"{settings.api_prefix}/monitor/run", status_code=202)
async def legacy_manual_monitor_run(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        return await start_bulk_check(
            requested_by=user["username"],
            ip_address=client_ip(request),
            interval_seconds=get_bulk_check_interval_seconds(
                settings.bulk_check_interval_seconds
            ),
        )
    except BulkCheckBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/monitor/runs")
def legacy_monitor_runs(
    limit: int = 20,
    _: dict = Depends(get_current_user),
) -> list[dict]:
    return list_jobs(limit)


@app.get(f"{settings.api_prefix}/cloudflare/accounts", response_model=list[CloudflareAccountResponse])
def cf_accounts(_: dict = Depends(get_current_user)) -> list[CloudflareAccountResponse]:
    return [CloudflareAccountResponse(**account) for account in list_cloudflare_accounts()]


@app.post(f"{settings.api_prefix}/cloudflare/accounts", response_model=CloudflareAccountResponse, status_code=status.HTTP_201_CREATED)
def cf_create_account(payload: CloudflareAccountCreateRequest, request: Request, user: dict = Depends(get_current_user)) -> CloudflareAccountResponse:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Cloudflare 账户名称不能为空")
    try:
        token = normalize_api_token(payload.api_token)
        verified = verify_token(token)
        verify_zone_access(token)
    except CloudflareError as exc:
        add_audit_log(
            user["username"],
            "CLOUDFLARE_ACCOUNT_IMPORT_FAILED",
            client_ip(request),
            str(exc),
            "CLOUDFLARE_ACCOUNT",
        )
        raise HTTPException(status_code=400, detail=f"Cloudflare Token 验证失败：{exc}") from exc
    account = create_cloudflare_account(name, payload.email or verified.get("email"), encrypt_secret(token))
    add_audit_log(user["username"], "CLOUDFLARE_ACCOUNT_CREATED", client_ip(request), name, "CLOUDFLARE_ACCOUNT", str(account["id"]))
    return CloudflareAccountResponse(**account)


@app.post(f"{settings.api_prefix}/cloudflare/accounts/{{account_id}}/test", response_model=MessageResponse)
def cf_test_account(account_id: int, request: Request, user: dict = Depends(get_current_user)) -> MessageResponse:
    account = get_cloudflare_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Cloudflare 账户不存在")
    try:
        token = decrypt_secret(account["api_token_encrypted"])
        result = verify_token(token)
        verify_zone_access(token)
        update_cloudflare_account_check(account_id, result.get("email"), None, datetime.now(timezone.utc).isoformat())
    except CloudflareError as exc:
        error = str(exc)
        update_cloudflare_account_check(account_id, None, error, datetime.now(timezone.utc).isoformat())
        raise HTTPException(status_code=400, detail=f"Cloudflare 连接失败：{error}") from exc
    add_audit_log(user["username"], "CLOUDFLARE_ACCOUNT_TESTED", client_ip(request), account["name"], "CLOUDFLARE_ACCOUNT", str(account_id))
    return MessageResponse(ok=True, message="Cloudflare Token 可用")


@app.delete(f"{settings.api_prefix}/cloudflare/accounts/{{account_id}}", response_model=MessageResponse)
def cf_delete_account(account_id: int, request: Request, user: dict = Depends(get_current_user)) -> MessageResponse:
    account = get_cloudflare_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Cloudflare 账户不存在")
    delete_cloudflare_account(account_id)
    add_audit_log(user["username"], "CLOUDFLARE_ACCOUNT_DELETED", client_ip(request), account["name"], "CLOUDFLARE_ACCOUNT", str(account_id))
    return MessageResponse(ok=True, message="Cloudflare 账户已删除")


@app.post(f"{settings.api_prefix}/cloudflare/accounts/{{account_id}}/zones/sync")
def cf_sync_zones(account_id: int, request: Request, user: dict = Depends(get_current_user)) -> list[dict]:
    if not get_cloudflare_account(account_id):
        raise HTTPException(status_code=404, detail="Cloudflare 账户不存在")
    try:
        zones = sync_zones(account_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"同步域名失败：{type(exc).__name__}: {exc}") from exc
    add_audit_log(user["username"], "CLOUDFLARE_ZONES_SYNCED", client_ip(request), f"count={len(zones)}", "CLOUDFLARE_ZONE", str(account_id))
    return list_zones(account_id)


@app.get(f"{settings.api_prefix}/cloudflare/accounts/{{account_id}}/zones")
def cf_list_zones(account_id: int, _: dict = Depends(get_current_user)) -> list[dict]:
    if not get_cloudflare_account(account_id):
        raise HTTPException(status_code=404, detail="Cloudflare 账户不存在")
    return list_zones(account_id)


@app.get(f"{settings.api_prefix}/cloudflare/accounts/{{account_id}}/zones/{{zone_id}}/dns-records")
def cf_list_dns(account_id: int, zone_id: str, _: dict = Depends(get_current_user)) -> list[dict]:
    try:
        return list_dns_records(account_id, zone_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取 DNS 记录失败：{type(exc).__name__}: {exc}") from exc


@app.post(f"{settings.api_prefix}/cloudflare/accounts/{{account_id}}/zones/{{zone_id}}/dns-records")
def cf_create_dns(account_id: int, zone_id: str, payload: DnsRecordRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        result = create_dns_record(account_id, zone_id, payload.model_dump(exclude_none=True))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"新增 DNS 记录失败：{type(exc).__name__}: {exc}") from exc
    add_audit_log(user["username"], "CLOUDFLARE_DNS_CREATED", client_ip(request), f"{payload.type} {payload.name}", "CLOUDFLARE_DNS", result.get("id"))
    return result


@app.put(f"{settings.api_prefix}/cloudflare/accounts/{{account_id}}/zones/{{zone_id}}/dns-records/{{record_id}}")
def cf_update_dns(account_id: int, zone_id: str, record_id: str, payload: DnsRecordRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        result = update_dns_record(account_id, zone_id, record_id, payload.model_dump(exclude_none=True))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"更新 DNS 记录失败：{type(exc).__name__}: {exc}") from exc
    add_audit_log(user["username"], "CLOUDFLARE_DNS_UPDATED", client_ip(request), f"{payload.type} {payload.name}", "CLOUDFLARE_DNS", record_id)
    return result


@app.delete(f"{settings.api_prefix}/cloudflare/accounts/{{account_id}}/zones/{{zone_id}}/dns-records/{{record_id}}", response_model=MessageResponse)
def cf_delete_dns(account_id: int, zone_id: str, record_id: str, request: Request, user: dict = Depends(get_current_user)) -> MessageResponse:
    try:
        delete_dns_record(account_id, zone_id, record_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"删除 DNS 记录失败：{type(exc).__name__}: {exc}") from exc
    add_audit_log(user["username"], "CLOUDFLARE_DNS_DELETED", client_ip(request), record_id, "CLOUDFLARE_DNS", record_id)
    return MessageResponse(ok=True, message="DNS 记录已删除")


@app.get(f"{settings.api_prefix}/audit-logs")
def audit_logs(
    limit: int = 100,
    resource_type: str | None = None,
    resource_id: str | None = None,
    _: dict = Depends(get_current_user),
) -> list[dict]:
    return [
        present_audit_log(row)
        for row in list_audit_logs(
            limit, resource_type=resource_type, resource_id=resource_id
        )
    ]


@app.get(f"{settings.api_prefix}/system/resources")
def system_resources(_: dict = Depends(get_current_user)) -> dict:
    return collect_resource_status()


@app.get(f"{settings.api_prefix}/system/resources/limits")
def system_resource_limits(_: dict = Depends(get_current_user)) -> dict:
    return load_resource_limits()


@app.put(f"{settings.api_prefix}/system/resources/limits")
def update_system_resource_limits(
    payload: ResourceLimitsRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    saved = save_resource_limits(payload.model_dump())
    add_audit_log(
        user["username"], "SYSTEM_RESOURCE_LIMITS_UPDATED", client_ip(request),
        json.dumps(saved, ensure_ascii=False), "SYSTEM_SETTINGS", "resources",
    )
    return saved


@app.get(f"{settings.api_prefix}/system/monitor/current")
def system_monitor_current(_: dict = Depends(get_current_user)) -> dict:
    return current_monitor_snapshot()


@app.get(f"{settings.api_prefix}/system/monitor/history")
def system_monitor_history(
    range: str = "1h",
    _: dict = Depends(get_current_user),
) -> dict:
    return monitor_history(range)


@app.get(f"{settings.api_prefix}/system/monitor/history.csv")
def system_monitor_history_csv(
    request: Request,
    range: str = "30d",
    user: dict = Depends(get_current_user),
) -> Response:
    content = export_history_csv(range)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    add_audit_log(
        user["username"], "SYSTEM_METRICS_EXPORTED", client_ip(request),
        f"range={range}", "SYSTEM_MONITOR",
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="OCI-NT-metrics-{range}-{stamp}.csv"'},
    )


@app.get(f"{settings.api_prefix}/system/monitor/settings")
def system_monitor_settings(_: dict = Depends(get_current_user)) -> dict:
    return {**load_monitor_settings(), "interfaces": list_network_interfaces()}


@app.put(f"{settings.api_prefix}/system/monitor/settings")
def update_system_monitor_settings(
    payload: MonitorSettingsRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        saved = save_monitor_settings(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    add_audit_log(
        user["username"], "SYSTEM_MONITOR_SETTINGS_UPDATED", client_ip(request),
        json.dumps(saved, ensure_ascii=False), "SYSTEM_SETTINGS", "monitor",
    )
    return {**saved, "interfaces": list_network_interfaces()}


@app.post(f"{settings.api_prefix}/system/monitor/traffic/reset")
def system_monitor_traffic_reset(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    result = reset_traffic_baseline()
    add_audit_log(
        user["username"], "SYSTEM_TRAFFIC_BASELINE_RESET", client_ip(request),
        result["reset_at"], "SYSTEM_MONITOR", "traffic",
    )
    return {"ok": True, **result}


@app.get(f"{settings.api_prefix}/system/monitor/alerts")
def system_monitor_alerts(_: dict = Depends(get_current_user)) -> dict:
    return list_monitor_alerts(limit=100)


@app.post(f"{settings.api_prefix}/system/monitor/history/cleanup")
def system_monitor_history_cleanup(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    result = cleanup_monitor_history()
    add_audit_log(
        user["username"], "SYSTEM_METRICS_CLEANED", client_ip(request),
        json.dumps(result, ensure_ascii=False), "SYSTEM_MONITOR", "history",
    )
    return {"ok": True, **result}


@app.get(f"{settings.api_prefix}/system/release")
def system_release(_: dict = Depends(get_current_user)) -> dict:
    return current_release_info()


@app.get(f"{settings.api_prefix}/system/release/history")
def system_release_history(
    limit: int = 30,
    _: dict = Depends(get_current_user),
) -> list[dict]:
    return list_release_history(limit=limit)


@app.get(f"{settings.api_prefix}/system/diagnostics")
def system_diagnostics(_: dict = Depends(get_current_user)) -> dict:
    return collect_system_diagnostics()


@app.get(f"{settings.api_prefix}/system/diagnostics/export")
def export_system_diagnostics(
    request: Request,
    user: dict = Depends(get_current_user),
) -> Response:
    bundle = collect_support_bundle()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"OCI-NT-diagnostics-{stamp}.json"
    add_audit_log(
        user["username"], "SYSTEM_DIAGNOSTICS_EXPORTED", client_ip(request),
        f"schema={bundle['database']['schema_version']},tasks={len(bundle['tasks']['recent'])}",
        "SYSTEM_DIAGNOSTICS",
    )
    return Response(
        content=json.dumps(bundle, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get(f"{settings.api_prefix}/system/backups/schedule")
def backup_schedule(_: dict = Depends(get_current_user)) -> dict:
    return schedule_status()


@app.put(f"{settings.api_prefix}/system/backups/schedule")
def update_backup_schedule(
    payload: BackupScheduleRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    previous = schedule_status()
    try:
        save_backup_schedule(
            enabled=payload.enabled,
            frequency=payload.frequency,
            local_time=payload.local_time,
            weekday=payload.weekday,
            timezone_offset_minutes=payload.timezone_offset_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = schedule_status()
    schedule_changed = any(
        previous.get(key) != result.get(key)
        for key in ("frequency", "local_time", "weekday", "timezone_offset_minutes")
    )
    if result["enabled"] and (not previous.get("enabled") or schedule_changed):
        result = mark_current_schedule_slot_handled()
    add_audit_log(
        user["username"], "SYSTEM_BACKUP_SCHEDULE_UPDATED", client_ip(request),
        f"enabled={result['enabled']},frequency={result['frequency']},time={result['local_time']},weekday={result['weekday']},offset={result['timezone_offset_minutes']}",
        "SYSTEM_BACKUP",
    )
    return result


@app.get(f"{settings.api_prefix}/system/backups")
def backups(_: dict = Depends(get_current_user)) -> list[dict]:
    return list_backups()


@app.get(f"{settings.api_prefix}/system/backups/policy")
def backup_policy(_: dict = Depends(get_current_user)) -> dict:
    policy = load_backup_policy()
    rows = list_backups()
    return {
        **policy,
        "current_count": len(rows),
        "current_bytes": sum(int(item.get("size") or 0) for item in rows),
    }


@app.put(f"{settings.api_prefix}/system/backups/policy")
def update_backup_policy(
    payload: BackupPolicyRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        saved = save_backup_policy(
            retention_days=payload.retention_days,
            keep_latest=payload.keep_latest,
            max_count=payload.max_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    add_audit_log(
        user["username"], "SYSTEM_BACKUP_POLICY_UPDATED", client_ip(request),
        f"days={saved['retention_days']},keep={saved['keep_latest']},max={saved['max_count']}",
        "SYSTEM_BACKUP",
    )
    return saved


@app.post(f"{settings.api_prefix}/system/backups/cleanup")
def cleanup_backups_route(
    payload: BackupCleanupRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    result = cleanup_old_backups(dry_run=payload.dry_run)
    add_audit_log(
        user["username"],
        "SYSTEM_BACKUP_CLEANUP_PREVIEW" if payload.dry_run else "SYSTEM_BACKUPS_CLEANED",
        client_ip(request),
        f"candidates={result['candidate_count']},deleted={result['deleted_count']},remaining={result['remaining_count']}",
        "SYSTEM_BACKUP",
    )
    return result


@app.post(f"{settings.api_prefix}/system/backups", response_model=dict)
def create_backup(request: Request, user: dict = Depends(get_current_user)) -> dict:
    backup = create_sqlite_backup("manual")
    add_audit_log(user["username"], "SYSTEM_BACKUP_CREATED", client_ip(request), backup["name"], "SYSTEM_BACKUP")
    send_configured_telegram(
        f"OCI-N&T 系统备份完成\n\n系统版本：{settings.display_version}\n备份文件：" + backup["name"],
        "system_backup",
    )
    return backup


@app.delete(f"{settings.api_prefix}/system/backups/{{backup_name}}", response_model=MessageResponse)
def delete_backup(backup_name: str, request: Request, user: dict = Depends(get_current_user)) -> MessageResponse:
    try:
        backup = delete_sqlite_backup(backup_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="备份文件不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    add_audit_log(user["username"], "SYSTEM_BACKUP_DELETED", client_ip(request), backup["name"], "SYSTEM_BACKUP")
    send_configured_telegram(
        f"OCI-N&T 系统备份已删除\n\n系统版本：{settings.display_version}\n备份文件：" + backup["name"],
        "system_backup",
    )
    return MessageResponse(ok=True, message=f"备份已删除：{backup['name']}")


@app.get(f"{settings.api_prefix}/system/database/restores")
def database_restore_history(
    limit: int = 50,
    _: dict = Depends(get_current_user),
) -> list[dict]:
    return list_database_restores(limit=limit)


@app.post(f"{settings.api_prefix}/system/backups/{{backup_name}}/restore")
async def formal_database_restore(
    backup_name: str,
    payload: DatabaseRestoreRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    stored = get_user_by_username(user["username"])
    if not stored or not verify_password(payload.current_password, stored["password_hash"]):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    try:
        result = await asyncio.to_thread(
            restore_database_backup,
            backup_name,
            requested_by=user["username"],
            expected_confirmation=payload.confirmation,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit_log(
        user["username"], "SYSTEM_DATABASE_RESTORED", client_ip(request),
        f"backup={backup_name},protection={result.get('protection_backup_name')}",
        "SYSTEM_BACKUP", backup_name,
    )
    return {
        **result,
        "message": "数据库恢复成功，所有旧登录会话已失效，请重新登录",
        "requires_relogin": True,
    }


@app.get(f"{settings.api_prefix}/system/backups/restore-drills")
def backup_restore_drill_history(
    limit: int = 30,
    _: dict = Depends(get_current_user),
) -> list[dict]:
    return list_restore_drills(limit=limit)


@app.post(f"{settings.api_prefix}/system/backups/{{backup_name}}/restore-drill")
async def backup_restore_drill_route(
    backup_name: str,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        result = await asyncio.to_thread(run_backup_restore_drill, backup_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="备份文件不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"恢复演练失败：{type(exc).__name__}: {exc}",
        ) from exc
    add_audit_log(
        user["username"],
        "SYSTEM_BACKUP_RESTORE_DRILL_SUCCESS" if result.get("status") == "SUCCESS" else "SYSTEM_BACKUP_RESTORE_DRILL_FAILED",
        client_ip(request),
        f"backup={backup_name},status={result.get('status')},schema={result.get('schema_after')},paths={result.get('openapi_paths')}",
        "SYSTEM_BACKUP", backup_name,
    )
    return result


@app.post(f"{settings.api_prefix}/system/backups/{{backup_name}}/verify")
def verify_backup_route(
    backup_name: str,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        result = verify_sqlite_backup(backup_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="备份文件不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    add_audit_log(
        user["username"], "SYSTEM_BACKUP_VERIFIED", client_ip(request),
        f"backup={backup_name},ok={result['ok']},sha256={result['sha256']}",
        "SYSTEM_BACKUP", backup_name,
    )
    return {"name": backup_name, **result}


@app.post(f"{settings.api_prefix}/system/database/maintain")
def maintain_database_route(
    payload: DatabaseMaintenanceRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        result = maintain_sqlite_database(vacuum=payload.vacuum)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"数据库检查与优化失败：{type(exc).__name__}: {exc}",
        ) from exc
    add_audit_log(
        user["username"], "SYSTEM_DATABASE_MAINTAINED", client_ip(request),
        f"vacuum={result['vacuum']},before={result['database_bytes_before']},after={result['database_bytes_after']}",
        "SYSTEM_DATABASE",
    )
    return result

# BEGIN OCI-N&T V1.0.4 1.0.4-identity-domain-policy-a5.5.1
from .identity_domain_password_policy_service import (
    list_identity_domain_password_policies,
    update_identity_domain_password_policy,
)


@app.get(
    f"{settings.api_prefix}/accounts/{{account_id}}/iam/identity-domain/password-policies"
)
def identity_domain_password_policies(
    account_id: int,
    _: dict = Depends(get_current_user),
) -> dict:
    try:
        return list_identity_domain_password_policies(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"读取 Identity Domains 密码策略失败：{type(exc).__name__}: {exc}",
        ) from exc


@app.put(
    f"{settings.api_prefix}/accounts/{{account_id}}/iam/identity-domain/password-policies/{{domain_id}}/{{policy_id}}"
)
def update_identity_domain_password_policy_route(
    account_id: int,
    domain_id: str,
    policy_id: str,
    payload: dict,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        updated = update_identity_domain_password_policy(
            account_id,
            domain_id,
            policy_id,
            payload,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"更新 Identity Domains 密码策略失败：{type(exc).__name__}: {exc}",
        ) from exc

    add_audit_log(
        user["username"],
        "OCI_IDENTITY_DOMAIN_PASSWORD_POLICY_UPDATED",
        client_ip(request),
        f"account={account_id},domain={domain_id},policy={policy_id}",
        "OCI_ACCOUNT",
        str(account_id),
    )
    return {"ok": True, "policy": updated}
# END OCI-N&T V1.0.4 1.0.4-identity-domain-policy-a5.5.1

# BEGIN OCI-N&T V1.0.4 1.0.4-final-stabilization1
from typing import Any as _FinalAny
from fastapi import Depends as _FinalDepends, HTTPException as _FinalHTTPException, Request as _FinalRequest
from pydantic import BaseModel as _FinalBaseModel
from .account_read_cache_service import (
    delete_account_read_cache as _final_delete_account_read_cache,
    ensure_account_read_cache_schema as _final_ensure_account_read_cache_schema,
    get_account_read_cache as _final_get_account_read_cache,
    list_account_read_cache_summary as _final_list_account_read_cache_summary,
    save_account_read_cache as _final_save_account_read_cache,
)
from .backup_integrity_service import verify_sqlite_backup as _final_verify_sqlite_backup

class _FinalReadCachePayload(_FinalBaseModel):
    payload: _FinalAny
    key: str = "default"
    source: str = "OCI"
    read_at: str | None = None

@app.get(f"{settings.api_prefix}/accounts/{{account_id}}/read-cache-summary")
def final_get_account_read_cache_summary(account_id: int, _: dict = _FinalDepends(get_current_user)) -> dict:
    try: return {"account_id":account_id,"categories":_final_list_account_read_cache_summary(account_id)}
    except KeyError as exc: raise _FinalHTTPException(status_code=404,detail=str(exc).strip("'")) from exc

@app.get(f"{settings.api_prefix}/accounts/{{account_id}}/read-cache/{{category}}")
def final_get_account_read_cache(account_id: int, category: str, key: str = "default", _: dict = _FinalDepends(get_current_user)) -> dict:
    try: entry=_final_get_account_read_cache(account_id,category,key)
    except KeyError as exc: raise _FinalHTTPException(status_code=404,detail=str(exc).strip("'")) from exc
    except ValueError as exc: raise _FinalHTTPException(status_code=400,detail=str(exc)) from exc
    return {"cached":False,"account_id":account_id,"category":category,"key":key} if entry is None else {"cached":True,**entry}

@app.put(f"{settings.api_prefix}/accounts/{{account_id}}/read-cache/{{category}}")
def final_put_account_read_cache(account_id: int, category: str, payload: _FinalReadCachePayload, _: dict = _FinalDepends(get_current_user)) -> dict:
    try:
        entry=_final_save_account_read_cache(account_id,category,payload.payload,cache_key=payload.key,source=payload.source,read_at=payload.read_at)
    except KeyError as exc: raise _FinalHTTPException(status_code=404,detail=str(exc).strip("'")) from exc
    except (TypeError,ValueError) as exc: raise _FinalHTTPException(status_code=400,detail=str(exc)) from exc
    return {"cached":True,**entry}

@app.delete(f"{settings.api_prefix}/accounts/{{account_id}}/read-cache/{{category}}")
def final_delete_account_read_cache(account_id: int, category: str, key: str | None = None, _: dict = _FinalDepends(get_current_user)) -> dict:
    try: removed=_final_delete_account_read_cache(account_id,category,key)
    except KeyError as exc: raise _FinalHTTPException(status_code=404,detail=str(exc).strip("'")) from exc
    except ValueError as exc: raise _FinalHTTPException(status_code=400,detail=str(exc)) from exc
    return {"ok":True,"removed":removed}

@app.post(f"{settings.api_prefix}/system/backups/{{backup_name}}/verify")
def final_verify_backup(backup_name: str, request: _FinalRequest, user: dict = _FinalDepends(get_current_user)) -> dict:
    try: result=_final_verify_sqlite_backup(backup_name)
    except FileNotFoundError as exc: raise _FinalHTTPException(status_code=404,detail="备份文件不存在") from exc
    except ValueError as exc: raise _FinalHTTPException(status_code=400,detail=str(exc)) from exc
    add_audit_log(user["username"],"SYSTEM_BACKUP_VERIFIED",client_ip(request),f"name={backup_name},ok={result['ok']}","SYSTEM_BACKUP",backup_name)
    if not result["ok"]: raise _FinalHTTPException(status_code=409,detail={"message":"备份完整性验证未通过","result":result})
    return result

_final_ensure_account_read_cache_schema()
# END OCI-N&T V1.0.4 1.0.4-final-stabilization1

# BEGIN OCI-N&T V1.0.4 1.0.4-adaptive-launch-c2
@app.get(f"{settings.api_prefix}/launch/scheduler/settings")
def launch_scheduler_settings(
    _: dict = Depends(get_current_user),
) -> dict:
    return adaptive_scheduler.scheduler_settings_payload()


@app.put(f"{settings.api_prefix}/launch/scheduler/settings")
def save_launch_scheduler_settings(
    payload: dict,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        result = adaptive_scheduler.update_scheduler_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    add_audit_log(
        user["username"],
        "LAUNCH_SCHEDULER_UPDATED",
        client_ip(request),
        "智能请求调度参数已更新",
        "SYSTEM_SETTING",
    )
    return result


@app.post(f"{settings.api_prefix}/launch/scheduler/settings/reset")
def reset_launch_scheduler_settings(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    result = adaptive_scheduler.reset_scheduler_config()
    add_audit_log(
        user["username"],
        "LAUNCH_SCHEDULER_RESET",
        client_ip(request),
        "智能请求调度参数已恢复默认值",
        "SYSTEM_SETTING",
    )
    return result


@app.get(f"{settings.api_prefix}/launch/scheduler/status")
def launch_scheduler_status(
    _: dict = Depends(get_current_user),
) -> dict:
    return adaptive_scheduler.scheduler_status()
# END OCI-N&T V1.0.4 1.0.4-adaptive-launch-c2
