import json
import secrets
from datetime import datetime, timezone

from .database import database

ACTIVE_STATUSES = {"PENDING", "RUNNING", "WAITING", "CANCELLING"}
TERMINAL_STATUSES = {"COMPLETED", "CANCELLED", "FAILED", "INTERRUPTED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_catalog_snapshot(
    *,
    account_id: int,
    region: str,
    compartment_ids: list[str],
    availability_domains: list[str],
) -> str:
    token = secrets.token_urlsafe(32)
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO launch_catalog_snapshots (
                token, account_id, region, compartment_ids_json,
                availability_domains_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                account_id,
                region,
                json.dumps(compartment_ids, ensure_ascii=False),
                json.dumps(availability_domains, ensure_ascii=False),
                now,
            ),
        )
    return token


def get_catalog_snapshot(token: str) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM launch_catalog_snapshots WHERE token = ?",
            (token,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["compartment_ids"] = json.loads(item.pop("compartment_ids_json"))
        item["availability_domains"] = json.loads(
            item.pop("availability_domains_json")
        )
    except (TypeError, json.JSONDecodeError):
        return None
    return item


def save_catalog_resources(
    *,
    token: str,
    compartment_id: str,
    availability_domain: str,
    subnet_ids: list[str],
    image_ids: list[str],
    shape_names: list[str],
) -> None:
    with database() as connection:
        connection.execute(
            """
            INSERT INTO launch_catalog_resources (
                token, compartment_id, availability_domain,
                subnet_ids_json, image_ids_json, shape_names_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token, compartment_id, availability_domain)
            DO UPDATE SET
                subnet_ids_json = excluded.subnet_ids_json,
                image_ids_json = excluded.image_ids_json,
                shape_names_json = excluded.shape_names_json,
                created_at = excluded.created_at
            """,
            (
                token,
                compartment_id,
                availability_domain,
                json.dumps(subnet_ids, ensure_ascii=False),
                json.dumps(image_ids, ensure_ascii=False),
                json.dumps(shape_names, ensure_ascii=False),
                utc_now(),
            ),
        )


def get_catalog_resources(
    token: str,
    compartment_id: str,
    availability_domain: str,
) -> dict | None:
    with database() as connection:
        row = connection.execute(
            """
            SELECT * FROM launch_catalog_resources
            WHERE token = ? AND compartment_id = ? AND availability_domain = ?
            """,
            (token, compartment_id, availability_domain),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    for source, target in (
        ("subnet_ids_json", "subnet_ids"),
        ("image_ids_json", "image_ids"),
        ("shape_names_json", "shape_names"),
    ):
        try:
            item[target] = json.loads(item.pop(source))
        except (TypeError, json.JSONDecodeError):
            return None
    return item


def create_launch_job(
    *,
    account_id: int,
    requested_by: str,
    mode: str,
    request_payload: dict,
    requested_count: int,
    max_attempts: int,
    retry_interval_seconds: int,
    concurrency: int,
) -> dict:
    now = utc_now()
    with database() as connection:
        active = connection.execute(
            """
            SELECT id FROM launch_jobs
            WHERE account_id = ?
              AND status IN ('PENDING','RUNNING','WAITING','CANCELLING')
            ORDER BY id DESC LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        if active:
            raise RuntimeError("当前租户已有运行中的创建或抢机任务")
        cursor = connection.execute(
            """
            INSERT INTO launch_jobs (
                account_id, requested_by, mode, status, request_json,
                requested_count, max_attempts, retry_interval_seconds,
                concurrency, created_at
            ) VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                requested_by,
                mode,
                json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")),
                requested_count,
                max_attempts,
                retry_interval_seconds,
                concurrency,
                now,
            ),
        )
        job_id = int(cursor.lastrowid)
    return get_launch_job(job_id) or {"id": job_id}


def get_launch_job(job_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM launch_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return _present_job(dict(row)) if row else None


def list_launch_jobs(account_id: int, limit: int = 50) -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            """
            SELECT * FROM launch_jobs
            WHERE account_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (account_id, max(1, min(int(limit), 200))),
        ).fetchall()
    return [_present_job(dict(row)) for row in rows]


def list_launch_attempts(job_id: int) -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            """
            SELECT * FROM launch_attempts
            WHERE job_id = ? ORDER BY id ASC
            """,
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def start_launch_job(job_id: int) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE launch_jobs
            SET status = 'RUNNING', started_at = COALESCE(started_at, ?),
                next_attempt_at = NULL
            WHERE id = ?
            """,
            (utc_now(), job_id),
        )


def set_launch_waiting(job_id: int, next_attempt_at: str) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE launch_jobs
            SET status = 'WAITING', next_attempt_at = ?
            WHERE id = ?
            """,
            (next_attempt_at, job_id),
        )


def save_launch_progress(
    job_id: int,
    *,
    current_attempt: int,
    success_count: int,
    failure_count: int,
    last_error: str | None,
) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE launch_jobs
            SET status = 'RUNNING', current_attempt = ?, success_count = ?,
                failure_count = ?, last_error = ?, next_attempt_at = NULL
            WHERE id = ?
            """,
            (
                current_attempt,
                success_count,
                failure_count,
                last_error[:2000] if last_error else None,
                job_id,
            ),
        )


def add_launch_attempt(
    *,
    job_id: int,
    round_no: int,
    sequence_no: int,
    status: str,
    display_name: str,
    instance_id: str | None = None,
    lifecycle_state: str | None = None,
    error: str | None = None,
    retryable: bool = False,
) -> None:
    with database() as connection:
        connection.execute(
            """
            INSERT INTO launch_attempts (
                job_id, round_no, sequence_no, status, display_name,
                instance_id, lifecycle_state, error, retryable, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                round_no,
                sequence_no,
                status,
                display_name,
                instance_id,
                lifecycle_state,
                error[:4000] if error else None,
                1 if retryable else 0,
                utc_now(),
            ),
        )


def request_launch_cancel(job_id: int, account_id: int) -> bool:
    with database() as connection:
        cursor = connection.execute(
            """
            UPDATE launch_jobs
            SET cancel_requested = 1,
                status = CASE WHEN status = 'WAITING' THEN 'CANCELLING' ELSE status END
            WHERE id = ? AND account_id = ?
              AND status IN ('PENDING','RUNNING','WAITING','CANCELLING')
            """,
            (job_id, account_id),
        )
        return cursor.rowcount > 0


def launch_cancel_requested(job_id: int) -> bool:
    with database() as connection:
        row = connection.execute(
            "SELECT cancel_requested FROM launch_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return bool(row and row["cancel_requested"])


def finish_launch_job(
    job_id: int,
    *,
    status: str,
    success_count: int,
    failure_count: int,
    last_error: str | None = None,
) -> None:
    if status not in TERMINAL_STATUSES:
        raise ValueError("任务结束状态不正确")
    with database() as connection:
        connection.execute(
            """
            UPDATE launch_jobs
            SET status = ?, success_count = ?, failure_count = ?,
                last_error = ?, finished_at = ?, next_attempt_at = NULL
            WHERE id = ?
            """,
            (
                status,
                success_count,
                failure_count,
                last_error[:2000] if last_error else None,
                utc_now(),
                job_id,
            ),
        )


def _present_job(item: dict) -> dict:
    try:
        item["request"] = json.loads(item.pop("request_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        item["request"] = {}
    item["cancel_requested"] = bool(item.get("cancel_requested"))

    # 加密后的 root 密码也不允许通过任务详情接口返回前端。
    request_payload = item.get("request") or {}
    if isinstance(request_payload, dict):
        request_payload.pop("root_password", None)
        request_payload.pop("root_password_encrypted", None)

    requested = max(0, int(item.get("requested_count") or 0))
    success = max(0, int(item.get("success_count") or 0))
    item["progress_percent"] = round((success / requested) * 100, 1) if requested else 0
    item["is_active"] = item.get("status") in ACTIVE_STATUSES
    return item


def reset_launch_job_counters(job_id: int, account_id: int) -> dict:
    with database() as connection:
        row = connection.execute(
            "SELECT status FROM launch_jobs WHERE id = ? AND account_id = ?",
            (int(job_id), int(account_id)),
        ).fetchone()
        if not row:
            raise KeyError("任务不存在或不属于当前租户")
        if row["status"] in ACTIVE_STATUSES:
            raise ValueError("运行中的任务不能重置统计")
        connection.execute(
            """
            UPDATE launch_jobs
            SET current_attempt = 0, failure_count = 0, last_error = NULL
            WHERE id = ? AND account_id = ?
            """,
            (int(job_id), int(account_id)),
        )
        connection.execute("DELETE FROM launch_attempts WHERE job_id = ?", (int(job_id),))
    return get_launch_job(int(job_id)) or {"id": int(job_id)}


def delete_launch_job(job_id: int, account_id: int) -> dict:
    """Delete one finished launch job and its attempts atomically."""
    with database() as connection:
        row = connection.execute(
            "SELECT id, status FROM launch_jobs WHERE id = ? AND account_id = ?",
            (int(job_id), int(account_id)),
        ).fetchone()
        if not row:
            raise KeyError("任务不存在或不属于当前租户")
        status = str(row["status"] or "").upper()
        if status in ACTIVE_STATUSES:
            raise ValueError("运行中的任务不能删除，请先取消并等待任务结束")
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"任务状态 {status or 'UNKNOWN'} 暂不允许删除")
        # Keep this explicit even though the foreign key also uses CASCADE.
        # It makes cleanup deterministic on installations migrated from older DBs.
        connection.execute("DELETE FROM launch_attempts WHERE job_id = ?", (int(job_id),))
        cursor = connection.execute(
            "DELETE FROM launch_jobs WHERE id = ? AND account_id = ?",
            (int(job_id), int(account_id)),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("任务删除失败")
    return {"ok": True, "id": int(job_id), "status": status}
