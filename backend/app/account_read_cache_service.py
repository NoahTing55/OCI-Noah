from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .database import database

CACHE_CATEGORIES = {
    "iam", "password-policy", "identity-domain-password-policy",
    "regions", "limits", "audit",
    "network", "security", "boot-volumes", "images", "vnc",
    "object-storage", "metrics", "costs", "launch-catalog",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _category(value: str) -> str:
    category = str(value or "").strip().lower()
    if category not in CACHE_CATEGORIES:
        raise ValueError(f"不支持的缓存分类：{category or '-'}")
    return category


def _cache_key(value: str | None) -> str:
    key = str(value or "default").strip() or "default"
    if len(key) > 1500:
        raise ValueError("缓存键过长")
    return key


def _ensure_account(connection, account_id: int) -> None:
    row = connection.execute("SELECT 1 FROM oci_accounts WHERE id = ?", (int(account_id),)).fetchone()
    if not row:
        raise KeyError("OCI 账户不存在")


def ensure_account_read_cache_schema() -> None:
    with database() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS oci_account_read_cache (
                account_id INTEGER NOT NULL REFERENCES oci_accounts(id) ON DELETE CASCADE,
                category TEXT NOT NULL,
                cache_key TEXT NOT NULL DEFAULT 'default',
                payload_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'OCI',
                read_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account_id, category, cache_key)
            )
            """
        )
        columns={str(row[1]) for row in connection.execute("PRAGMA table_info(oci_account_read_cache)").fetchall()}
        if "cache_key" not in columns:
            raise RuntimeError("oci_account_read_cache 为旧结构；请先删除该实验缓存表后重试")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_oci_account_read_cache_updated ON oci_account_read_cache(updated_at)")


def get_account_read_cache(account_id: int, category: str, cache_key: str = "default") -> dict | None:
    category=_category(category); cache_key=_cache_key(cache_key); ensure_account_read_cache_schema()
    with database() as connection:
        _ensure_account(connection, account_id)
        row=connection.execute(
            """SELECT account_id,category,cache_key,payload_json,source,read_at,updated_at
               FROM oci_account_read_cache WHERE account_id=? AND category=? AND cache_key=?""",
            (int(account_id),category,cache_key),
        ).fetchone()
    if not row: return None
    try: payload=json.loads(row["payload_json"])
    except Exception as exc: raise ValueError(f"缓存 JSON 已损坏：{category}") from exc
    return {"account_id":int(row["account_id"]),"category":row["category"],"key":row["cache_key"],"payload":payload,"source":row["source"],"read_at":row["read_at"],"updated_at":row["updated_at"]}


def save_account_read_cache(account_id: int, category: str, payload: Any, *, cache_key: str = "default", source: str = "OCI", read_at: str | None = None) -> dict:
    category=_category(category); cache_key=_cache_key(cache_key); ensure_account_read_cache_schema()
    saved_at=str(read_at or _utc_now()).strip() or _utc_now(); source_value=str(source or "OCI").strip()[:40] or "OCI"
    payload_json=json.dumps(payload,ensure_ascii=False,separators=(",",":"),allow_nan=False)
    with database() as connection:
        _ensure_account(connection,account_id)
        connection.execute(
            """INSERT INTO oci_account_read_cache(account_id,category,cache_key,payload_json,source,read_at,updated_at)
               VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(account_id,category,cache_key) DO UPDATE SET
                 payload_json=excluded.payload_json,source=excluded.source,read_at=excluded.read_at,updated_at=CURRENT_TIMESTAMP""",
            (int(account_id),category,cache_key,payload_json,source_value,saved_at),
        )
    result=get_account_read_cache(account_id,category,cache_key)
    if result is None: raise RuntimeError("SQLite 缓存保存后无法重新读取")
    return result


def delete_account_read_cache(account_id: int, category: str, cache_key: str | None = None) -> int:
    category=_category(category); ensure_account_read_cache_schema()
    with database() as connection:
        _ensure_account(connection,account_id)
        if cache_key is None:
            cursor=connection.execute("DELETE FROM oci_account_read_cache WHERE account_id=? AND category=?",(int(account_id),category))
        else:
            cursor=connection.execute("DELETE FROM oci_account_read_cache WHERE account_id=? AND category=? AND cache_key=?",(int(account_id),category,_cache_key(cache_key)))
        return int(cursor.rowcount or 0)


def list_account_read_cache_summary(account_id: int) -> dict:
    ensure_account_read_cache_schema()
    with database() as connection:
        _ensure_account(connection,account_id)
        rows=connection.execute(
            """SELECT category,COUNT(*) AS item_count,MAX(read_at) AS read_at,MAX(updated_at) AS updated_at
               FROM oci_account_read_cache WHERE account_id=? GROUP BY category ORDER BY category""",
            (int(account_id),),
        ).fetchall()
    return {str(row["category"]):{"count":int(row["item_count"]),"read_at":row["read_at"],"updated_at":row["updated_at"]} for row in rows}
