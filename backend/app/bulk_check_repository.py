import json
from datetime import datetime, timezone

from .database import database

ACTIVE_STATUSES = {"PENDING", "RUNNING", "WAITING"}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    *,
    requested_by: str,
    interval_seconds: int,
    total: int,
) -> dict:
    created_at = utc_now()
    with database() as connection:
        active = connection.execute(
            """
            SELECT id FROM bulk_check_jobs
            WHERE status IN ('PENDING', 'RUNNING', 'WAITING')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if active:
            raise RuntimeError(f"已有检测任务正在运行（#{active['id']}）")
        cursor = connection.execute(
            """
            INSERT INTO bulk_check_jobs (
                status, requested_by, interval_seconds, total, created_at
            ) VALUES ('PENDING', ?, ?, ?, ?)
            """,
            (requested_by, interval_seconds, total, created_at),
        )
        job_id = int(cursor.lastrowid)
    result = get_job(job_id)
    if not result:
        raise RuntimeError("无法创建检测任务")
    return result


def get_job(job_id: int) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM bulk_check_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return _present(dict(row)) if row else None


def get_current_job() -> dict | None:
    with database() as connection:
        row = connection.execute(
            """
            SELECT * FROM bulk_check_jobs
            ORDER BY
                CASE WHEN status IN ('PENDING', 'RUNNING', 'WAITING') THEN 0 ELSE 1 END,
                id DESC
            LIMIT 1
            """
        ).fetchone()
    return _present(dict(row)) if row else None


def list_jobs(limit: int = 20) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    with database() as connection:
        rows = connection.execute(
            "SELECT * FROM bulk_check_jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_present(dict(row)) for row in rows]


def start_job(job_id: int) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE bulk_check_jobs
            SET status = 'RUNNING', started_at = ?, error = NULL
            WHERE id = ? AND status = 'PENDING'
            """,
            (utc_now(), job_id),
        )


def set_current_account(
    job_id: int,
    *,
    current_index: int,
    account_id: int,
    account_name: str,
) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE bulk_check_jobs
            SET status = 'RUNNING', current_index = ?,
                current_account_id = ?, current_account_name = ?,
                next_account_at = NULL
            WHERE id = ?
            """,
            (current_index, account_id, account_name, job_id),
        )


def set_waiting(job_id: int, next_account_at: str) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE bulk_check_jobs
            SET status = 'WAITING', next_account_at = ?,
                current_account_id = NULL, current_account_name = NULL
            WHERE id = ?
            """,
            (next_account_at, job_id),
        )


def save_progress(
    job_id: int,
    *,
    alive: int,
    abnormal: int,
    unknown: int,
    results: list[dict],
) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE bulk_check_jobs
            SET alive = ?, abnormal = ?, unknown = ?, results_json = ?
            WHERE id = ?
            """,
            (
                alive,
                abnormal,
                unknown,
                json.dumps(results, ensure_ascii=False, separators=(",", ":")),
                job_id,
            ),
        )


def request_cancel(job_id: int) -> bool:
    with database() as connection:
        cursor = connection.execute(
            """
            UPDATE bulk_check_jobs
            SET cancel_requested = 1
            WHERE id = ? AND status IN ('PENDING', 'RUNNING', 'WAITING')
            """,
            (job_id,),
        )
        return cursor.rowcount > 0


def is_cancel_requested(job_id: int) -> bool:
    with database() as connection:
        row = connection.execute(
            "SELECT cancel_requested FROM bulk_check_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return bool(row and row["cancel_requested"])


def finish_job(
    job_id: int,
    *,
    status: str,
    alive: int,
    abnormal: int,
    unknown: int,
    results: list[dict],
    error: str | None = None,
) -> None:
    if status not in TERMINAL_STATUSES:
        raise ValueError("任务结束状态不正确")
    with database() as connection:
        connection.execute(
            """
            UPDATE bulk_check_jobs
            SET status = ?, finished_at = ?, alive = ?, abnormal = ?,
                unknown = ?, results_json = ?, error = ?,
                next_account_at = NULL, current_account_id = NULL,
                current_account_name = NULL
            WHERE id = ?
            """,
            (
                status,
                utc_now(),
                alive,
                abnormal,
                unknown,
                json.dumps(results, ensure_ascii=False, separators=(",", ":")),
                error[:2000] if error else None,
                job_id,
            ),
        )


def _present(row: dict) -> dict:
    try:
        row["results"] = json.loads(row.pop("results_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        row["results"] = []
    row["cancel_requested"] = bool(row.get("cancel_requested"))
    total = int(row.get("total") or 0)
    current_index = int(row.get("current_index") or 0)
    row["completed_count"] = len(row["results"])
    row["progress_percent"] = (
        round((row["completed_count"] / total) * 100, 1) if total else 0
    )
    row["is_active"] = row.get("status") in ACTIVE_STATUSES
    row["current_index"] = current_index
    return row
