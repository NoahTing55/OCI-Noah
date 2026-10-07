import asyncio
from datetime import datetime, timedelta, timezone

from .account_check_service import execute_account_check, mark_account_check_error
from .account_repository import list_accounts_public
from .bulk_check_repository import (
    create_job,
    finish_job,
    get_job,
    is_cancel_requested,
    request_cancel,
    save_progress,
    set_current_account,
    set_waiting,
    start_job,
)
from .database import add_audit_log
from .telegram_service import format_bulk_check_message, send_configured_telegram


class BulkCheckBusyError(RuntimeError):
    pass


_tasks: dict[int, asyncio.Task] = {}


def _counts(results: list[dict]) -> tuple[int, int, int]:
    alive = sum(1 for item in results if item.get("account_status") == "ALIVE")
    unknown = sum(1 for item in results if item.get("account_status") == "UNKNOWN")
    abnormal = max(0, len(results) - alive - unknown)
    return alive, abnormal, unknown


def _result_snapshot(account: dict) -> dict:
    return {
        "id": int(account["id"]),
        "custom_name": account.get("custom_name"),
        "email": account.get("email"),
        "tenancy_name": account.get("tenancy_name"),
        "home_region_name": account.get("home_region_name"),
        "home_region_key": account.get("home_region_key"),
        "account_status": account.get("account_status") or "UNKNOWN",
        "last_error": account.get("last_error"),
        "last_checked_at": account.get("last_checked_at"),
    }


async def start_bulk_check(
    *,
    requested_by: str,
    ip_address: str | None,
    interval_seconds: int,
) -> dict:
    interval_seconds = int(interval_seconds)
    if interval_seconds < 0:
        raise ValueError("全部账户检测间隔不能小于 0 秒")
    if interval_seconds > 86400:
        raise ValueError("全部账户检测间隔不能超过 86400 秒")

    accounts = list_accounts_public()
    try:
        job = create_job(
            requested_by=requested_by,
            interval_seconds=interval_seconds,
            total=len(accounts),
        )
    except RuntimeError as exc:
        raise BulkCheckBusyError(str(exc)) from exc

    task = asyncio.create_task(
        _run_bulk_check(
            job_id=int(job["id"]),
            accounts=accounts,
            requested_by=requested_by,
            ip_address=ip_address,
            interval_seconds=interval_seconds,
        )
    )
    _tasks[int(job["id"])] = task
    task.add_done_callback(lambda _: _tasks.pop(int(job["id"]), None))
    return get_job(int(job["id"])) or job


async def _run_bulk_check(
    *,
    job_id: int,
    accounts: list[dict],
    requested_by: str,
    ip_address: str | None,
    interval_seconds: int,
) -> None:
    results: list[dict] = []
    try:
        start_job(job_id)
        if not accounts:
            finish_job(
                job_id,
                status="COMPLETED",
                alive=0,
                abnormal=0,
                unknown=0,
                results=[],
            )
            return

        for index, account in enumerate(accounts, start=1):
            if is_cancel_requested(job_id):
                alive, abnormal, unknown = _counts(results)
                finish_job(
                    job_id,
                    status="CANCELLED",
                    alive=alive,
                    abnormal=abnormal,
                    unknown=unknown,
                    results=results,
                )
                add_audit_log(
                    requested_by,
                    "OCI_ACCOUNTS_BULK_CHECK_CANCELLED",
                    ip_address,
                    f"job={job_id},completed={len(results)},total={len(accounts)}",
                    "BULK_ACCOUNT_CHECK",
                    str(job_id),
                )
                return

            set_current_account(
                job_id,
                current_index=index,
                account_id=int(account["id"]),
                account_name=account.get("custom_name") or str(account["id"]),
            )
            try:
                checked = await asyncio.to_thread(
                    execute_account_check,
                    int(account["id"]),
                )
            except Exception as exc:
                checked = await asyncio.to_thread(
                    mark_account_check_error,
                    account,
                    f"{type(exc).__name__}: {exc}",
                )

            results.append(_result_snapshot(checked))
            alive, abnormal, unknown = _counts(results)
            save_progress(
                job_id,
                alive=alive,
                abnormal=abnormal,
                unknown=unknown,
                results=results,
            )

            if index < len(accounts):
                next_at = datetime.now(timezone.utc) + timedelta(
                    seconds=interval_seconds
                )
                set_waiting(job_id, next_at.isoformat())
                # Check cancellation every second without making any OCI request.
                for _ in range(interval_seconds):
                    if is_cancel_requested(job_id):
                        alive, abnormal, unknown = _counts(results)
                        finish_job(
                            job_id,
                            status="CANCELLED",
                            alive=alive,
                            abnormal=abnormal,
                            unknown=unknown,
                            results=results,
                        )
                        add_audit_log(
                            requested_by,
                            "OCI_ACCOUNTS_BULK_CHECK_CANCELLED",
                            ip_address,
                            (
                                f"job={job_id},completed={len(results)},"
                                f"total={len(accounts)}"
                            ),
                            "BULK_ACCOUNT_CHECK",
                            str(job_id),
                        )
                        return
                    await asyncio.sleep(1)

        alive, abnormal, unknown = _counts(results)
        finish_job(
            job_id,
            status="COMPLETED",
            alive=alive,
            abnormal=abnormal,
            unknown=unknown,
            results=results,
        )
        message = format_bulk_check_message(
            len(results),
            alive,
            abnormal,
            unknown,
            results,
        )
        await asyncio.to_thread(send_configured_telegram, message)
        add_audit_log(
            requested_by,
            "OCI_ACCOUNTS_BULK_CHECKED",
            ip_address,
            (
                f"job={job_id},total={len(results)},alive={alive},"
                f"abnormal={abnormal},unknown={unknown},interval={interval_seconds}"
            ),
            "BULK_ACCOUNT_CHECK",
            str(job_id),
        )
    except asyncio.CancelledError:
        alive, abnormal, unknown = _counts(results)
        finish_job(
            job_id,
            status="INTERRUPTED",
            alive=alive,
            abnormal=abnormal,
            unknown=unknown,
            results=results,
            error="任务进程被终止；系统不会自动恢复 OCI 查询",
        )
        raise
    except Exception as exc:
        alive, abnormal, unknown = _counts(results)
        finish_job(
            job_id,
            status="FAILED",
            alive=alive,
            abnormal=abnormal,
            unknown=unknown,
            results=results,
            error=f"{type(exc).__name__}: {exc}",
        )
        add_audit_log(
            requested_by,
            "OCI_ACCOUNTS_BULK_CHECK_FAILED",
            ip_address,
            f"job={job_id},{type(exc).__name__}: {exc}",
            "BULK_ACCOUNT_CHECK",
            str(job_id),
        )


def cancel_bulk_check(job_id: int) -> bool:
    return request_cancel(job_id)
