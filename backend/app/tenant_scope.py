from fastapi import HTTPException

from .account_repository import get_account_public
from .instance_cache_repository import (
    cached_boot_volume_belongs_to_account,
    cached_private_ip_belongs_to_account,
    get_cached_instance,
)


def require_account(account_id: int) -> dict:
    account = get_account_public(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="OCI 账户不存在")
    return account


def require_instance(account_id: int, instance_id: str) -> dict:
    require_account(account_id)
    instance = get_cached_instance(account_id, instance_id)
    if not instance:
        raise HTTPException(
            status_code=409,
            detail=(
                "该实例不在当前租户的本地缓存中。请先手动同步当前租户实例，"
                "系统不会后台查询其他租户。"
            ),
        )
    return instance


def require_private_ip(account_id: int, private_ip_id: str) -> None:
    require_account(account_id)
    if not cached_private_ip_belongs_to_account(account_id, private_ip_id):
        raise HTTPException(
            status_code=409,
            detail="该私网 IP 不属于当前租户缓存，请先同步当前租户实例",
        )


def require_boot_volume(account_id: int, volume_id: str) -> None:
    require_account(account_id)
    if not cached_boot_volume_belongs_to_account(account_id, volume_id):
        raise HTTPException(
            status_code=409,
            detail="该引导卷不属于当前租户缓存，请先同步当前租户实例",
        )


def require_instance_region(
    account_id: int,
    instance_id: str,
    region: str | None,
) -> dict:
    instance = require_instance(account_id, instance_id)
    cached_region = str(instance.get("region") or "").strip().lower()
    requested_region = str(region or "").strip().lower()
    if requested_region and cached_region and requested_region != cached_region:
        raise HTTPException(
            status_code=409,
            detail=(
                f"区域不匹配：当前租户缓存中的实例位于 {cached_region}，"
                f"请求区域为 {requested_region}"
            ),
        )
    return instance
