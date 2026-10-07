from datetime import datetime, timezone

from cryptography.fernet import InvalidToken

from .account_repository import get_account_with_secrets, update_account_check
from .credential_crypto import decrypt_secret
from .oci_service import check_oci_credentials


def _account_values(account: dict) -> dict:
    return {
        "email": account.get("email"),
        "user_name": account.get("user_name"),
        "user_lifecycle_state": account.get("user_lifecycle_state"),
        "user_time_created": account.get("user_time_created"),
        "tenancy_name": account.get("tenancy_name"),
        "home_region_key": account.get("home_region_key"),
        "home_region_name": account.get("home_region_name"),
        "account_type": account.get("account_type") or "UNKNOWN",
        "subscription_plan_type": account.get("subscription_plan_type"),
        "subscription_upgrade_state": account.get("subscription_upgrade_state"),
        "subscription_time_start": account.get("subscription_time_start"),
    }


def mark_account_check_error(account: dict, error: str) -> dict:
    values = _account_values(account)
    values.update(
        {
            "account_status": "ERROR",
            "last_error": error[:1000],
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    updated = update_account_check(int(account["id"]), **values)
    if not updated:
        raise KeyError("OCI 账户不存在")
    return updated


def execute_account_check(account_id: int) -> dict:
    account = get_account_with_secrets(account_id)
    if not account:
        raise KeyError("OCI 账户不存在")

    try:
        private_key_pem = decrypt_secret(account["private_key_encrypted"])
        passphrase = (
            decrypt_secret(account["passphrase_encrypted"])
            if account.get("passphrase_encrypted")
            else None
        )
        encrypted_proxy = account.get("profile_proxy_url_encrypted") or account.get("proxy_url_encrypted")
        proxy_url = (
            decrypt_secret(encrypted_proxy)
            if account.get("proxy_enabled") and encrypted_proxy
            else None
        )
    except InvalidToken as exc:
        raise RuntimeError(
            "OCI 凭据或代理无法解密，请检查 CREDENTIAL_ENCRYPTION_KEY"
        ) from exc

    result = check_oci_credentials(
        tenancy_ocid=account["tenancy_ocid"],
        user_ocid=account["user_ocid"],
        fingerprint=account["fingerprint"],
        region=account["region"],
        private_key_pem=private_key_pem,
        private_key_passphrase=passphrase,
        proxy_url=proxy_url,
    )
    values = _account_values(account)
    values.update(
        {
            "email": result.email or account.get("email"),
            "user_name": result.user_name or account.get("user_name"),
            "user_lifecycle_state": (
                result.user_lifecycle_state or account.get("user_lifecycle_state")
            ),
            "user_time_created": (
                result.user_time_created or account.get("user_time_created")
            ),
            "tenancy_name": result.tenancy_name or account.get("tenancy_name"),
            "home_region_key": (
                result.home_region_key or account.get("home_region_key")
            ),
            "home_region_name": (
                result.home_region_name or account.get("home_region_name")
            ),
            "account_type": (
                result.account_type
                if result.account_type != "UNKNOWN"
                else account.get("account_type") or "UNKNOWN"
            ),
            "subscription_plan_type": (
                result.subscription_plan_type
                or account.get("subscription_plan_type")
            ),
            "subscription_upgrade_state": (
                result.subscription_upgrade_state
                or account.get("subscription_upgrade_state")
            ),
            "subscription_time_start": (
                result.subscription_time_start
                or account.get("subscription_time_start")
            ),
            "account_status": result.status,
            "last_error": result.error,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    updated = update_account_check(account_id, **values)
    if not updated:
        raise KeyError("OCI 账户不存在")
    return updated
