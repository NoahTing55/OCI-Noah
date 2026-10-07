from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .database import database

ACTIVE_STATUSES = {"PENDING", "RUNNING", "WAITING", "CANCELLING"}
TERMINAL_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED"}
ITEM_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "SKIPPED", "CANCELLED", "INTERRUPTED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str | None, fallback: object) -> object:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def _present_item(row: dict) -> dict:
    item = dict(row)
    item["payload"] = _json_load(item.pop("payload_json", "{}"), {})
    item["result"] = _json_load(item.pop("result_json", "{}"), {})
    item["retryable"] = bool(item.get("retryable"))
    return item


def _present_task(row: dict, items: list[dict] | None = None) -> dict:
    task = dict(row)
    task["options"] = _json_load(task.pop("options_json", "{}"), {})
    task["summary"] = _json_load(task.pop("summary_json", "{}"), {})
    task["cancel_requested"] = bool(task.get("cancel_requested"))
    task["is_active"] = task.get("status") in ACTIVE_STATUSES
    task["can_cancel"] = task["is_active"] and not task["cancel_requested"]
    task["interrupted"] = max(0, int(task.get("interrupted") or 0))
    task["can_retry"] = (
        task.get("status") in TERMINAL_STATUSES
        and (int(task.get("failed") or 0) + task["interrupted"]) > 0
    )
    total = max(0, int(task.get("total") or 0))
    completed = max(0, int(task.get("completed") or 0))
    task["progress_percent"] = round((completed / total) * 100, 1) if total else 0.0
    if items is not None:
        task["items"] = items
        categories: dict[str, int] = {}
        for item in items:
            category = str(item.get("error_category") or "").strip().upper()
            if category and item.get("status") in {"FAILED", "INTERRUPTED", "SKIPPED"}:
                categories[category] = categories.get(category, 0) + 1
        task["error_categories"] = categories
    return task


def build_request_fingerprint(task_type: str, *, items: Iterable[dict], options: dict | None = None) -> str:
    normalized_items = []
    for item in items:
        normalized_items.append({
            "item_key": str(item.get("item_key") or ""),
            "payload": item.get("payload") or {},
        })
    normalized_items.sort(key=lambda item: item["item_key"] )
    payload = {
        "task_type": str(task_type).strip().upper(),
        "items": normalized_items,
        "options": options or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_duplicate_task(
    *,
    request_fingerprint: str | None = None,
    idempotency_key: str | None = None,
    fingerprint_window_seconds: int = 20,
    idempotency_window_hours: int = 24,
) -> dict | None:
    now = datetime.now(timezone.utc)
    clauses: list[str] = []
    params: list[object] = []
    if idempotency_key:
        clauses.append("(idempotency_key = ? AND created_at >= ?)")
        params.extend([
            str(idempotency_key).strip()[:200],
            (now - timedelta(hours=max(1, idempotency_window_hours))).isoformat(),
        ])
    if request_fingerprint:
        clauses.append(
            "(request_fingerprint = ? AND (status IN ('PENDING','RUNNING','WAITING','CANCELLING') OR created_at >= ?))"
        )
        params.extend([
            str(request_fingerprint).strip(),
            (now - timedelta(seconds=max(1, fingerprint_window_seconds))).isoformat(),
        ])
    if not clauses:
        return None
    with database() as connection:
        row = connection.execute(
            f"SELECT * FROM manual_tasks WHERE {' OR '.join(clauses)} ORDER BY id DESC LIMIT 1",
            tuple(params),
        ).fetchone()
    if not row:
        return None
    task = get_task(int(row["id"]))
    if task:
        task["deduplicated"] = True
    return task


def create_task(
    *,
    task_type: str,
    title: str,
    requested_by: str,
    items: Iterable[dict],
    options: dict | None = None,
    retry_of_task_id: int | None = None,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
    dedupe_seconds: int = 20,
) -> dict:
    normalized_items = list(items)
    normalized_type = str(task_type).strip().upper()
    normalized_options = options or {}
    fingerprint = request_fingerprint or build_request_fingerprint(
        normalized_type, items=normalized_items, options=normalized_options
    )
    normalized_idempotency = str(idempotency_key or "").strip()[:200] or None
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    dedupe_until = (now_dt + timedelta(seconds=max(1, int(dedupe_seconds)))).isoformat()
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        clauses: list[str] = []
        params: list[object] = []
        if normalized_idempotency:
            clauses.append("(idempotency_key = ? AND created_at >= ?)")
            params.extend([normalized_idempotency, (now_dt - timedelta(hours=24)).isoformat()])
        clauses.append(
            "(request_fingerprint = ? AND (status IN ('PENDING','RUNNING','WAITING','CANCELLING') OR created_at >= ?))"
        )
        params.extend([fingerprint, (now_dt - timedelta(seconds=max(1, int(dedupe_seconds)))).isoformat()])
        existing = connection.execute(
            f"SELECT id FROM manual_tasks WHERE {' OR '.join(clauses)} ORDER BY id DESC LIMIT 1",
            tuple(params),
        ).fetchone()
        if existing:
            existing_id = int(existing["id"])
            connection.commit()
            result = get_task(existing_id)
            if not result:
                raise RuntimeError("重复任务存在但无法读取")
            result["deduplicated"] = True
            return result
        cursor = connection.execute(
            """
            INSERT INTO manual_tasks (
                task_type, title, status, requested_by, total,
                retry_of_task_id, options_json, idempotency_key,
                request_fingerprint, dedupe_until, created_at, updated_at
            ) VALUES (?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_type,
                str(title).strip(),
                str(requested_by).strip() or "system",
                len(normalized_items),
                retry_of_task_id,
                _json_dump(normalized_options),
                normalized_idempotency,
                fingerprint,
                dedupe_until,
                now,
                now,
            ),
        )
        task_id = int(cursor.lastrowid)
        for index, item in enumerate(normalized_items, start=1):
            item_key = str(item.get("item_key") or index)
            connection.execute(
                """
                INSERT INTO manual_task_items (
                    task_id, item_key, item_name, item_type, status,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?)
                """,
                (
                    task_id,
                    item_key,
                    str(item.get("item_name") or item_key),
                    str(item.get("item_type") or task_type).upper(),
                    _json_dump(item.get("payload") or {}),
                    now,
                    now,
                ),
            )
    result = get_task(task_id)
    if not result:
        raise RuntimeError("任务创建后无法读取")
    result["deduplicated"] = False
    return result


def get_task(task_id: int, *, include_items: bool = True) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM manual_tasks WHERE id = ?", (int(task_id),)
        ).fetchone()
        if not row:
            return None
        items = None
        if include_items:
            item_rows = connection.execute(
                "SELECT * FROM manual_task_items WHERE task_id = ? ORDER BY id",
                (int(task_id),),
            ).fetchall()
            items = [_present_item(dict(item)) for item in item_rows]
    return _present_task(dict(row), items)


def list_tasks(
    *,
    limit: int = 50,
    task_type: str | None = None,
    status: str | None = None,
) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    clauses: list[str] = []
    params: list[object] = []
    if task_type:
        clauses.append("task_type = ?")
        params.append(str(task_type).strip().upper())
    if status:
        clauses.append("status = ?")
        params.append(str(status).strip().upper())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with database() as connection:
        rows = connection.execute(
            f"SELECT * FROM manual_tasks {where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    return [_present_task(dict(row)) for row in rows]


def get_current_task(task_type: str | None = None) -> dict | None:
    params: list[object] = []
    type_clause = ""
    if task_type:
        type_clause = "AND task_type = ?"
        params.append(str(task_type).strip().upper())
    with database() as connection:
        row = connection.execute(
            f"""
            SELECT * FROM manual_tasks
            WHERE status IN ('PENDING', 'RUNNING', 'WAITING', 'CANCELLING')
            {type_clause}
            ORDER BY id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    return _present_task(dict(row)) if row else None


def find_active_account_conflicts(
    account_ids: Iterable[int],
    *,
    exclude_task_id: int | None = None,
) -> list[dict]:
    wanted = {int(value) for value in account_ids if int(value) > 0}
    if not wanted:
        return []
    params: list[object] = []
    exclude = ""
    if exclude_task_id is not None:
        exclude = "AND task.id <> ?"
        params.append(int(exclude_task_id))
    with database() as connection:
        rows = connection.execute(
            f"""
            SELECT task.id, task.task_type, task.title, task.status,
                   item.payload_json
            FROM manual_tasks task
            JOIN manual_task_items item ON item.task_id = task.id
            WHERE task.status IN ('PENDING','RUNNING','WAITING','CANCELLING')
              AND task.task_type IN ('ACCOUNT_CHECK','INSTANCE_BATCH')
              {exclude}
            ORDER BY task.id DESC, item.id
            """,
            tuple(params),
        ).fetchall()

    grouped: dict[int, dict] = {}
    for row in rows:
        payload = _json_load(row["payload_json"], {})
        try:
            account_id = int((payload or {}).get("account_id") or 0)
        except (TypeError, ValueError):
            continue
        if account_id not in wanted:
            continue
        task_id = int(row["id"])
        entry = grouped.setdefault(task_id, {
            "id": task_id,
            "task_type": row["task_type"],
            "title": row["title"],
            "status": row["status"],
            "account_ids": [],
        })
        if account_id not in entry["account_ids"]:
            entry["account_ids"].append(account_id)
    return list(grouped.values())


def get_task_items(task_id: int, *, statuses: set[str] | None = None) -> list[dict]:
    params: list[object] = [int(task_id)]
    status_clause = ""
    if statuses:
        normalized = sorted({str(value).upper() for value in statuses})
        placeholders = ",".join("?" for _ in normalized)
        status_clause = f"AND status IN ({placeholders})"
        params.extend(normalized)
    with database() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM manual_task_items
            WHERE task_id = ? {status_clause}
            ORDER BY id
            """,
            tuple(params),
        ).fetchall()
    return [_present_item(dict(row)) for row in rows]


def start_task(task_id: int) -> None:
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE manual_tasks
            SET status = 'RUNNING', started_at = COALESCE(started_at, ?),
                updated_at = ?, error = NULL
            WHERE id = ? AND status = 'PENDING'
            """,
            (now, now, int(task_id)),
        )


def set_task_current(
    task_id: int,
    *,
    item_key: str | None,
    item_name: str | None,
    status: str = "RUNNING",
    next_item_at: str | None = None,
) -> None:
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE manual_tasks
            SET status = ?, current_item_key = ?, current_item_name = ?,
                next_item_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(status).upper(),
                item_key,
                item_name,
                next_item_at,
                now,
                int(task_id),
            ),
        )


def start_item(item_id: int) -> None:
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE manual_task_items
            SET status = 'RUNNING', attempt = attempt + 1,
                started_at = COALESCE(started_at, ?), finished_at = NULL,
                error = NULL, next_retry_at = NULL, updated_at = ?
            WHERE id = ? AND status IN ('PENDING', 'RUNNING', 'FAILED', 'INTERRUPTED')
            """,
            (now, now, int(item_id)),
        )


def set_item_retry_state(
    item_id: int,
    *,
    error_category: str,
    retryable: bool,
    next_retry_at: str,
    error: str,
) -> None:
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE manual_task_items
            SET error_category = ?, retryable = ?, next_retry_at = ?,
                error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(error_category).strip().upper(),
                1 if retryable else 0,
                next_retry_at,
                str(error)[:4000],
                now,
                int(item_id),
            ),
        )


def finish_item(
    item_id: int,
    *,
    status: str,
    result: dict | None = None,
    error: str | None = None,
    error_category: str | None = None,
    retryable: bool = False,
) -> None:
    normalized = str(status).upper()
    if normalized not in ITEM_TERMINAL_STATUSES:
        raise ValueError("任务项目结束状态不正确")
    now = utc_now()
    with database() as connection:
        row = connection.execute(
            "SELECT task_id FROM manual_task_items WHERE id = ?", (int(item_id),)
        ).fetchone()
        if not row:
            raise KeyError("任务项目不存在")
        task_id = int(row["task_id"])
        connection.execute(
            """
            UPDATE manual_task_items
            SET status = ?, result_json = ?, error = ?, error_category = ?,
                retryable = ?, next_retry_at = NULL, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                normalized,
                _json_dump(result or {}),
                str(error)[:4000] if error else None,
                str(error_category).strip().upper() if error_category else None,
                1 if retryable else 0,
                now,
                now,
                int(item_id),
            ),
        )
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN status IN ('SUCCEEDED','FAILED','SKIPPED','CANCELLED','INTERRUPTED') THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END) AS skipped,
                SUM(CASE WHEN status = 'INTERRUPTED' THEN 1 ELSE 0 END) AS interrupted
            FROM manual_task_items WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE manual_tasks
            SET completed = ?, succeeded = ?, failed = ?, skipped = ?,
                interrupted = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                int(counts["completed"] or 0),
                int(counts["succeeded"] or 0),
                int(counts["failed"] or 0),
                int(counts["skipped"] or 0),
                int(counts["interrupted"] or 0),
                now,
                task_id,
            ),
        )


def request_cancel(task_id: int) -> bool:
    now = utc_now()
    with database() as connection:
        cursor = connection.execute(
            """
            UPDATE manual_tasks
            SET cancel_requested = 1,
                status = CASE WHEN status = 'PENDING' THEN 'CANCELLING' ELSE status END,
                updated_at = ?
            WHERE id = ? AND status IN ('PENDING', 'RUNNING', 'WAITING', 'CANCELLING')
            """,
            (now, int(task_id)),
        )
    return cursor.rowcount > 0


def is_cancel_requested(task_id: int) -> bool:
    with database() as connection:
        row = connection.execute(
            "SELECT cancel_requested FROM manual_tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone()
    return bool(row and row["cancel_requested"])


def cancel_pending_items(task_id: int) -> None:
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE manual_task_items
            SET status = 'CANCELLED', error = COALESCE(error, '任务已取消'),
                finished_at = ?, updated_at = ?
            WHERE task_id = ? AND status = 'PENDING'
            """,
            (now, now, int(task_id)),
        )
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN status IN ('SUCCEEDED','FAILED','SKIPPED','CANCELLED','INTERRUPTED') THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END) AS skipped,
                SUM(CASE WHEN status = 'INTERRUPTED' THEN 1 ELSE 0 END) AS interrupted
            FROM manual_task_items WHERE task_id = ?
            """,
            (int(task_id),),
        ).fetchone()
        connection.execute(
            """
            UPDATE manual_tasks
            SET completed = ?, succeeded = ?, failed = ?, skipped = ?,
                interrupted = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                int(counts["completed"] or 0),
                int(counts["succeeded"] or 0),
                int(counts["failed"] or 0),
                int(counts["skipped"] or 0),
                int(counts["interrupted"] or 0),
                now,
                int(task_id),
            ),
        )


def finish_task(
    task_id: int,
    *,
    status: str | None = None,
    summary: dict | None = None,
    error: str | None = None,
) -> dict:
    task = get_task(task_id)
    if not task:
        raise KeyError("任务不存在")
    if status is None:
        if task["interrupted"]:
            status = "PARTIAL" if (task["succeeded"] or task["failed"]) else "INTERRUPTED"
        elif task["failed"] and task["succeeded"]:
            status = "PARTIAL"
        elif task["failed"]:
            status = "FAILED"
        else:
            status = "COMPLETED"
    normalized = str(status).upper()
    if normalized not in TERMINAL_STATUSES:
        raise ValueError("任务结束状态不正确")
    now = utc_now()
    with database() as connection:
        if normalized == "INTERRUPTED":
            connection.execute(
                """
                UPDATE manual_task_items
                SET status = 'INTERRUPTED', finished_at = ?, updated_at = ?,
                    error = COALESCE(error, ?)
                WHERE task_id = ? AND status IN ('PENDING', 'RUNNING')
                """,
                (now, now, str(error or "任务执行被中断")[:4000], int(task_id)),
            )
            counts = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status IN ('SUCCEEDED','FAILED','SKIPPED','CANCELLED','INTERRUPTED') THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END) AS skipped,
                    SUM(CASE WHEN status = 'INTERRUPTED' THEN 1 ELSE 0 END) AS interrupted
                FROM manual_task_items WHERE task_id = ?
                """,
                (int(task_id),),
            ).fetchone()
            connection.execute(
                """
                UPDATE manual_tasks
                SET completed = ?, succeeded = ?, failed = ?, skipped = ?, interrupted = ?
                WHERE id = ?
                """,
                (
                    int(counts["completed"] or 0),
                    int(counts["succeeded"] or 0),
                    int(counts["failed"] or 0),
                    int(counts["skipped"] or 0),
                    int(counts["interrupted"] or 0),
                    int(task_id),
                ),
            )
        connection.execute(
            """
            UPDATE manual_tasks
            SET status = ?, summary_json = ?, error = ?, finished_at = ?,
                current_item_key = NULL, current_item_name = NULL,
                next_item_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (
                normalized,
                _json_dump(summary or {}),
                str(error)[:4000] if error else None,
                now,
                now,
                int(task_id),
            ),
        )
    result = get_task(task_id)
    if not result:
        raise RuntimeError("任务结束后无法读取")
    return result


def delete_terminal_task(task_id: int) -> dict | None:
    task = get_task(task_id, include_items=False)
    if not task:
        return None
    if task.get("status") not in TERMINAL_STATUSES:
        raise ValueError("运行中的任务不能删除")
    with database() as connection:
        connection.execute("DELETE FROM manual_tasks WHERE id = ?", (int(task_id),))
    return task


def cleanup_terminal_tasks(*, older_than_days: int = 30, keep_latest: int = 100) -> dict:
    days = max(1, min(int(older_than_days), 3650))
    keep = max(0, min(int(keep_latest), 1000))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    terminal = sorted(TERMINAL_STATUSES)
    placeholders = ",".join("?" for _ in terminal)
    with database() as connection:
        rows = connection.execute(
            f"""
            SELECT id, status, finished_at, created_at
            FROM manual_tasks
            WHERE status IN ({placeholders})
            ORDER BY id DESC
            """,
            tuple(terminal),
        ).fetchall()
        candidates = []
        for row in rows[keep:]:
            finished = str(row["finished_at"] or row["created_at"] or "")
            if finished and finished < cutoff:
                candidates.append(int(row["id"]))
        if candidates:
            delete_marks = ",".join("?" for _ in candidates)
            connection.execute(
                f"DELETE FROM manual_tasks WHERE id IN ({delete_marks})",
                tuple(candidates),
            )
    return {
        "deleted": len(candidates),
        "older_than_days": days,
        "keep_latest": keep,
        "cutoff": cutoff,
    }
