from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    signature: str
    apply: Callable[[sqlite3.Connection], None]

    @property
    def checksum(self) -> str:
        raw = f"{self.version}:{self.name}:{self.signature}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    if column_name not in _column_names(connection, table_name):
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


def _execute_script(connection: sqlite3.Connection, script: str) -> None:
    statement = ""
    for line in script.splitlines():
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            statement = ""
            if sql:
                connection.execute(sql)
    if statement.strip():
        raise RuntimeError("数据库迁移 SQL 存在未结束语句")


BASELINE_SCHEMA_SQL = "CREATE TABLE IF NOT EXISTS users (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    username TEXT NOT NULL UNIQUE,\n    password_hash TEXT NOT NULL,\n    is_active INTEGER NOT NULL DEFAULT 1,\n    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP\n);\n\nCREATE TABLE IF NOT EXISTS system_settings (\n    setting_key TEXT PRIMARY KEY,\n    setting_value TEXT,\n    is_secret INTEGER NOT NULL DEFAULT 0,\n    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP\n);\n\nCREATE TABLE IF NOT EXISTS audit_logs (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    username TEXT,\n    action TEXT NOT NULL,\n    resource_type TEXT,\n    resource_id TEXT,\n    detail TEXT,\n    ip_address TEXT,\n    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP\n);\n\nCREATE TABLE IF NOT EXISTS oci_accounts (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    custom_name TEXT NOT NULL,\n    tenancy_ocid TEXT NOT NULL,\n    user_ocid TEXT NOT NULL,\n    fingerprint TEXT NOT NULL,\n    region TEXT NOT NULL,\n    private_key_encrypted TEXT NOT NULL,\n    passphrase_encrypted TEXT,\n    email TEXT,\n    user_name TEXT,\n    user_lifecycle_state TEXT,\n    user_time_created TEXT,\n    tenancy_name TEXT,\n    home_region_key TEXT,\n    home_region_name TEXT,\n    account_type TEXT NOT NULL DEFAULT 'UNKNOWN',\n    subscription_plan_type TEXT,\n    subscription_upgrade_state TEXT,\n    subscription_time_start TEXT,\n    proxy_enabled INTEGER NOT NULL DEFAULT 0,\n    proxy_url_encrypted TEXT,\n    proxy_label TEXT,\n    proxy_last_ip TEXT,\n    proxy_last_test_at TEXT,\n    proxy_last_error TEXT,\n    account_status TEXT NOT NULL DEFAULT 'UNKNOWN',\n    last_error TEXT,\n    last_checked_at TEXT,\n    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    UNIQUE (tenancy_ocid, user_ocid, fingerprint)\n);\n\nCREATE INDEX IF NOT EXISTS idx_oci_accounts_status\nON oci_accounts(account_status);\n\nCREATE INDEX IF NOT EXISTS idx_oci_accounts_email\nON oci_accounts(email);\n\nCREATE TABLE IF NOT EXISTS proxy_profiles (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    name TEXT NOT NULL COLLATE NOCASE UNIQUE,\n    proxy_url_encrypted TEXT NOT NULL,\n    scheme TEXT NOT NULL,\n    host TEXT NOT NULL,\n    port INTEGER NOT NULL,\n    has_auth INTEGER NOT NULL DEFAULT 0,\n    rotate_api_url_encrypted TEXT,\n    rotate_api_headers_encrypted TEXT,\n    rotate_api_method TEXT NOT NULL DEFAULT 'GET',\n    rotate_api_body_encrypted TEXT,\n    rotate_wait_seconds INTEGER NOT NULL DEFAULT 3,\n    last_rotate_at TEXT,\n    last_rotate_error TEXT,\n    last_ip TEXT,\n    last_test_at TEXT,\n    last_error TEXT,\n    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP\n);\n\nCREATE INDEX IF NOT EXISTS idx_proxy_profiles_name\nON proxy_profiles(name COLLATE NOCASE);\n\nCREATE TABLE IF NOT EXISTS cloudflare_accounts (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    name TEXT NOT NULL,\n    email TEXT,\n    api_token_encrypted TEXT NOT NULL,\n    last_error TEXT,\n    last_checked_at TEXT,\n    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP\n);\n\nCREATE TABLE IF NOT EXISTS cloudflare_zones (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    account_id INTEGER NOT NULL\n        REFERENCES cloudflare_accounts(id) ON DELETE CASCADE,\n    zone_id TEXT NOT NULL,\n    name TEXT NOT NULL,\n    status TEXT,\n    paused INTEGER NOT NULL DEFAULT 0,\n    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n    UNIQUE(account_id, zone_id)\n);\n\n-- Kept for data compatibility with earlier versions. v7 does not\n-- schedule these runs automatically.\nCREATE TABLE IF NOT EXISTS monitor_runs (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    trigger_type TEXT NOT NULL,\n    status TEXT NOT NULL,\n    started_at TEXT NOT NULL,\n    finished_at TEXT,\n    total INTEGER NOT NULL DEFAULT 0,\n    alive INTEGER NOT NULL DEFAULT 0,\n    abnormal INTEGER NOT NULL DEFAULT 0,\n    unknown INTEGER NOT NULL DEFAULT 0,\n    changes_count INTEGER NOT NULL DEFAULT 0,\n    notification_status TEXT NOT NULL DEFAULT 'SKIPPED',\n    notification_message TEXT,\n    error TEXT\n);\n\nCREATE INDEX IF NOT EXISTS idx_monitor_runs_started_at\nON monitor_runs(started_at DESC);\n\nCREATE TABLE IF NOT EXISTS oci_instance_cache (\n    account_id INTEGER NOT NULL\n        REFERENCES oci_accounts(id) ON DELETE CASCADE,\n    instance_id TEXT NOT NULL,\n    region TEXT NOT NULL,\n    payload_json TEXT NOT NULL,\n    synced_at TEXT NOT NULL,\n    PRIMARY KEY (account_id, instance_id)\n);\n\nCREATE INDEX IF NOT EXISTS idx_oci_instance_cache_region\nON oci_instance_cache(account_id, region);\n\nCREATE TABLE IF NOT EXISTS oci_instance_cache_syncs (\n    account_id INTEGER PRIMARY KEY\n        REFERENCES oci_accounts(id) ON DELETE CASCADE,\n    regions_scanned INTEGER NOT NULL DEFAULT 0,\n    errors_json TEXT NOT NULL DEFAULT '[]',\n    last_error TEXT,\n    synced_at TEXT NOT NULL\n);\n\nCREATE TABLE IF NOT EXISTS bulk_check_jobs (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    status TEXT NOT NULL,\n    requested_by TEXT NOT NULL,\n    interval_seconds INTEGER NOT NULL,\n    total INTEGER NOT NULL DEFAULT 0,\n    current_index INTEGER NOT NULL DEFAULT 0,\n    current_account_id INTEGER,\n    current_account_name TEXT,\n    next_account_at TEXT,\n    alive INTEGER NOT NULL DEFAULT 0,\n    abnormal INTEGER NOT NULL DEFAULT 0,\n    unknown INTEGER NOT NULL DEFAULT 0,\n    cancel_requested INTEGER NOT NULL DEFAULT 0,\n    results_json TEXT NOT NULL DEFAULT '[]',\n    error TEXT,\n    created_at TEXT NOT NULL,\n    started_at TEXT,\n    finished_at TEXT\n);\n\nCREATE INDEX IF NOT EXISTS idx_bulk_check_jobs_status\nON bulk_check_jobs(status, id DESC);\n\nCREATE TABLE IF NOT EXISTS oauth_login_codes (\n    code_hash TEXT PRIMARY KEY,\n    username TEXT NOT NULL,\n    expires_at TEXT NOT NULL,\n    used_at TEXT,\n    created_at TEXT NOT NULL\n);\n\nCREATE TABLE IF NOT EXISTS oauth_login_states (\n    state_hash TEXT PRIMARY KEY,\n    nonce TEXT NOT NULL,\n    expires_at TEXT NOT NULL,\n    used_at TEXT,\n    created_at TEXT NOT NULL\n);\n\nCREATE INDEX IF NOT EXISTS idx_oauth_login_states_expiry\nON oauth_login_states(expires_at, used_at);\n\nCREATE TABLE IF NOT EXISTS launch_catalog_snapshots (\n    token TEXT PRIMARY KEY,\n    account_id INTEGER NOT NULL\n        REFERENCES oci_accounts(id) ON DELETE CASCADE,\n    region TEXT NOT NULL,\n    compartment_ids_json TEXT NOT NULL,\n    availability_domains_json TEXT NOT NULL,\n    created_at TEXT NOT NULL\n);\n\nCREATE INDEX IF NOT EXISTS idx_launch_catalog_account\nON launch_catalog_snapshots(account_id, created_at DESC);\n\nCREATE TABLE IF NOT EXISTS launch_catalog_resources (\n    token TEXT NOT NULL\n        REFERENCES launch_catalog_snapshots(token) ON DELETE CASCADE,\n    compartment_id TEXT NOT NULL,\n    availability_domain TEXT NOT NULL,\n    subnet_ids_json TEXT NOT NULL,\n    image_ids_json TEXT NOT NULL,\n    shape_names_json TEXT NOT NULL,\n    created_at TEXT NOT NULL,\n    PRIMARY KEY(token, compartment_id, availability_domain)\n);\n\nCREATE TABLE IF NOT EXISTS launch_jobs (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    account_id INTEGER NOT NULL\n        REFERENCES oci_accounts(id) ON DELETE CASCADE,\n    requested_by TEXT NOT NULL,\n    mode TEXT NOT NULL,\n    status TEXT NOT NULL,\n    request_json TEXT NOT NULL,\n    requested_count INTEGER NOT NULL DEFAULT 1,\n    max_attempts INTEGER NOT NULL DEFAULT 1,\n    retry_interval_seconds INTEGER NOT NULL DEFAULT 30,\n    concurrency INTEGER NOT NULL DEFAULT 1,\n    current_attempt INTEGER NOT NULL DEFAULT 0,\n    success_count INTEGER NOT NULL DEFAULT 0,\n    failure_count INTEGER NOT NULL DEFAULT 0,\n    cancel_requested INTEGER NOT NULL DEFAULT 0,\n    next_attempt_at TEXT,\n    last_error TEXT,\n    created_at TEXT NOT NULL,\n    started_at TEXT,\n    finished_at TEXT\n);\n\nCREATE INDEX IF NOT EXISTS idx_launch_jobs_account_status\nON launch_jobs(account_id, status, id DESC);\n\nCREATE TABLE IF NOT EXISTS launch_attempts (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    job_id INTEGER NOT NULL\n        REFERENCES launch_jobs(id) ON DELETE CASCADE,\n    round_no INTEGER NOT NULL,\n    sequence_no INTEGER NOT NULL,\n    status TEXT NOT NULL,\n    display_name TEXT NOT NULL,\n    instance_id TEXT,\n    lifecycle_state TEXT,\n    error TEXT,\n    retryable INTEGER NOT NULL DEFAULT 0,\n    created_at TEXT NOT NULL\n);\n\nCREATE INDEX IF NOT EXISTS idx_launch_attempts_job\nON launch_attempts(job_id, id);\n\nCREATE TABLE IF NOT EXISTS oci_resource_cache (\n    account_id INTEGER NOT NULL\n        REFERENCES oci_accounts(id) ON DELETE CASCADE,\n    resource_type TEXT NOT NULL,\n    resource_id TEXT NOT NULL,\n    region TEXT,\n    payload_json TEXT NOT NULL,\n    synced_at TEXT NOT NULL,\n    PRIMARY KEY(account_id, resource_type, resource_id)\n);\n\nCREATE INDEX IF NOT EXISTS idx_oci_resource_cache_type\nON oci_resource_cache(account_id, resource_type, region, synced_at DESC);\n\nCREATE TABLE IF NOT EXISTS ip_quality_history (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    account_id INTEGER NOT NULL\n        REFERENCES oci_accounts(id) ON DELETE CASCADE,\n    instance_id TEXT NOT NULL,\n    private_ip_id TEXT NOT NULL,\n    public_ip TEXT,\n    region TEXT NOT NULL,\n    score INTEGER,\n    action TEXT NOT NULL,\n    payload_json TEXT NOT NULL DEFAULT '{}',\n    created_at TEXT NOT NULL\n);\n\nCREATE INDEX IF NOT EXISTS idx_ip_quality_history_account\nON ip_quality_history(account_id, instance_id, id DESC);\n\nCREATE TABLE IF NOT EXISTS vnc_sessions (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    account_id INTEGER NOT NULL\n        REFERENCES oci_accounts(id) ON DELETE CASCADE,\n    instance_id TEXT NOT NULL,\n    region TEXT NOT NULL,\n    console_connection_id TEXT NOT NULL,\n    token_hash TEXT NOT NULL UNIQUE,\n    local_port INTEGER NOT NULL,\n    status TEXT NOT NULL,\n    error TEXT,\n    created_at TEXT NOT NULL,\n    expires_at TEXT NOT NULL,\n    stopped_at TEXT\n);\n\nCREATE INDEX IF NOT EXISTS idx_vnc_sessions_account\nON vnc_sessions(account_id, instance_id, id DESC);\n\nCREATE TABLE IF NOT EXISTS object_multipart_sessions (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    account_id INTEGER NOT NULL\n        REFERENCES oci_accounts(id) ON DELETE CASCADE,\n    region TEXT NOT NULL,\n    namespace TEXT NOT NULL,\n    bucket_name TEXT NOT NULL,\n    object_name TEXT NOT NULL,\n    upload_id TEXT NOT NULL,\n    status TEXT NOT NULL,\n    parts_json TEXT NOT NULL DEFAULT '[]',\n    created_at TEXT NOT NULL,\n    updated_at TEXT NOT NULL\n);\n\nCREATE INDEX IF NOT EXISTS idx_multipart_sessions_account\nON object_multipart_sessions(account_id, status, id DESC);\n\nCREATE TABLE IF NOT EXISTS launch_profiles (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    account_id INTEGER NOT NULL\n        REFERENCES oci_accounts(id) ON DELETE CASCADE,\n    name TEXT NOT NULL,\n    payload_json TEXT NOT NULL,\n    enabled INTEGER NOT NULL DEFAULT 1,\n    created_at TEXT NOT NULL,\n    updated_at TEXT NOT NULL\n);\n\nCREATE INDEX IF NOT EXISTS idx_launch_profiles_account\nON launch_profiles(account_id, id DESC);\n"


TASK_CENTER_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS manual_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    succeeded INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    retry_of_task_id INTEGER REFERENCES manual_tasks(id) ON DELETE SET NULL,
    current_item_key TEXT,
    current_item_name TEXT,
    next_item_at TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_manual_tasks_status
ON manual_tasks(status, id DESC);

CREATE INDEX IF NOT EXISTS idx_manual_tasks_type
ON manual_tasks(task_type, id DESC);

CREATE TABLE IF NOT EXISTS manual_task_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES manual_tasks(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    item_name TEXT NOT NULL,
    item_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(task_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_manual_task_items_status
ON manual_task_items(task_id, status, id);

CREATE TABLE IF NOT EXISTS proxy_health_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy_profile_id INTEGER NOT NULL REFERENCES proxy_profiles(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES manual_tasks(id) ON DELETE SET NULL,
    success INTEGER NOT NULL DEFAULT 0,
    exit_ip TEXT,
    latency_ms INTEGER,
    country_code TEXT,
    country_name TEXT,
    region_name TEXT,
    city TEXT,
    error TEXT,
    tested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proxy_health_history_profile
ON proxy_health_history(proxy_profile_id, id DESC);

CREATE TABLE IF NOT EXISTS proxy_rotation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy_profile_id INTEGER NOT NULL REFERENCES proxy_profiles(id) ON DELETE CASCADE,
    success INTEGER NOT NULL DEFAULT 0,
    old_ip TEXT,
    new_ip TEXT,
    changed INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proxy_rotation_history_profile
ON proxy_rotation_history(proxy_profile_id, id DESC);
"""


def _migration_0001_baseline(connection: sqlite3.Connection) -> None:
    _execute_script(connection, BASELINE_SCHEMA_SQL)
    _ensure_column(connection, "users", "role", "TEXT NOT NULL DEFAULT 'ADMIN'")
    _ensure_column(connection, "users", "last_login_at", "TEXT")

    oci_columns = {
        "home_region_name": "TEXT",
        "account_type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "subscription_plan_type": "TEXT",
        "subscription_upgrade_state": "TEXT",
        "subscription_time_start": "TEXT",
        "proxy_enabled": "INTEGER NOT NULL DEFAULT 0",
        "proxy_url_encrypted": "TEXT",
        "proxy_label": "TEXT",
        "proxy_last_ip": "TEXT",
        "proxy_last_test_at": "TEXT",
        "proxy_last_error": "TEXT",
        "proxy_profile_id": "INTEGER REFERENCES proxy_profiles(id) ON DELETE SET NULL",
    }
    for column, definition in oci_columns.items():
        _ensure_column(connection, "oci_accounts", column, definition)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_oci_accounts_proxy_profile "
        "ON oci_accounts(proxy_profile_id)"
    )

    proxy_columns = {
        "rotate_api_url_encrypted": "TEXT",
        "rotate_api_headers_encrypted": "TEXT",
        "rotate_api_method": "TEXT NOT NULL DEFAULT 'GET'",
        "rotate_api_body_encrypted": "TEXT",
        "rotate_wait_seconds": "INTEGER NOT NULL DEFAULT 3",
        "last_rotate_at": "TEXT",
        "last_rotate_error": "TEXT",
    }
    for column, definition in proxy_columns.items():
        _ensure_column(connection, "proxy_profiles", column, definition)

    for column, definition in {
        "email": "TEXT",
        "last_error": "TEXT",
        "last_checked_at": "TEXT",
    }.items():
        _ensure_column(connection, "cloudflare_accounts", column, definition)


def _migration_0002_task_center(connection: sqlite3.Connection) -> None:
    _execute_script(connection, TASK_CENTER_SCHEMA_SQL)
    proxy_columns = {
        "last_latency_ms": "INTEGER",
        "last_country_code": "TEXT",
        "last_country_name": "TEXT",
        "last_region_name": "TEXT",
        "last_city": "TEXT",
        "last_success_at": "TEXT",
        "consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, definition in proxy_columns.items():
        _ensure_column(connection, "proxy_profiles", column, definition)


def _migration_0003_task_recovery_and_maintenance(connection: sqlite3.Connection) -> None:
    _ensure_column(
        connection,
        "manual_tasks",
        "interrupted",
        "INTEGER NOT NULL DEFAULT 0",
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_manual_tasks_finished "
        "ON manual_tasks(status, finished_at, id DESC)"
    )


def _migration_0004_v102_resilience_and_release_tracking(connection: sqlite3.Connection) -> None:
    task_columns = {
        "idempotency_key": "TEXT",
        "request_fingerprint": "TEXT",
        "dedupe_until": "TEXT",
    }
    for column, definition in task_columns.items():
        _ensure_column(connection, "manual_tasks", column, definition)
    item_columns = {
        "error_category": "TEXT",
        "retryable": "INTEGER NOT NULL DEFAULT 0",
        "next_retry_at": "TEXT",
    }
    for column, definition in item_columns.items():
        _ensure_column(connection, "manual_task_items", column, definition)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_manual_tasks_fingerprint "
        "ON manual_tasks(request_fingerprint, created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_manual_tasks_idempotency "
        "ON manual_tasks(idempotency_key, created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_manual_task_items_error_category "
        "ON manual_task_items(task_id, error_category, status)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS backup_restore_drills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_name TEXT NOT NULL,
            status TEXT NOT NULL,
            quick_check TEXT,
            schema_before INTEGER,
            schema_after INTEGER,
            openapi_paths INTEGER,
            app_version TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_backup_restore_drills_created "
        "ON backup_restore_drills(id DESC)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS system_upgrade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            build_id TEXT,
            source_fingerprint TEXT,
            previous_version TEXT,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            schema_version INTEGER,
            log_path TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_system_upgrade_history_created "
        "ON system_upgrade_history(id DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_system_upgrade_history_build "
        "ON system_upgrade_history(build_id, event_type, id DESC)"
    )



def _migration_0005_v103_recovery_sessions_and_resource_protection(connection: sqlite3.Connection) -> None:
    user_columns = {
        "token_version": "INTEGER NOT NULL DEFAULT 0",
        "failed_login_count": "INTEGER NOT NULL DEFAULT 0",
        "locked_until": "TEXT",
    }
    for column, definition in user_columns.items():
        _ensure_column(connection, "users", column, definition)

    item_columns = {
        "recovery_state": "TEXT",
        "recovery_note": "TEXT",
        "observed_state": "TEXT",
    }
    for column, definition in item_columns.items():
        _ensure_column(connection, "manual_task_items", column, definition)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            session_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            username TEXT NOT NULL,
            token_version INTEGER NOT NULL DEFAULT 0,
            login_method TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL,
            last_seen_at TEXT,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            revoke_reason TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_active "
        "ON auth_sessions(user_id, revoked_at, expires_at, last_seen_at DESC)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            ip_address TEXT,
            user_agent TEXT,
            success INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_user_created "
        "ON login_attempts(username, created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_created "
        "ON login_attempts(ip_address, created_at DESC)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS database_restore_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_name TEXT NOT NULL,
            protection_backup_name TEXT,
            status TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            schema_before INTEGER,
            schema_after INTEGER,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_database_restore_history_created "
        "ON database_restore_history(id DESC)"
    )


def _migration_0006_v104_system_monitoring(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            interface TEXT NOT NULL,
            cpu_percent REAL NOT NULL DEFAULT 0,
            load1 REAL NOT NULL DEFAULT 0,
            load5 REAL NOT NULL DEFAULT 0,
            load15 REAL NOT NULL DEFAULT 0,
            memory_total_bytes INTEGER NOT NULL DEFAULT 0,
            memory_used_bytes INTEGER NOT NULL DEFAULT 0,
            swap_total_bytes INTEGER NOT NULL DEFAULT 0,
            swap_used_bytes INTEGER NOT NULL DEFAULT 0,
            disk_total_bytes INTEGER NOT NULL DEFAULT 0,
            disk_used_bytes INTEGER NOT NULL DEFAULT 0,
            rx_total_bytes INTEGER NOT NULL DEFAULT 0,
            tx_total_bytes INTEGER NOT NULL DEFAULT 0,
            rx_delta_bytes INTEGER NOT NULL DEFAULT 0,
            tx_delta_bytes INTEGER NOT NULL DEFAULT 0,
            rx_rate_bps REAL NOT NULL DEFAULT 0,
            tx_rate_bps REAL NOT NULL DEFAULT 0,
            uptime_seconds INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_system_metrics_collected "
        "ON system_metrics(collected_at, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_system_metrics_interface_boot "
        "ON system_metrics(interface, boot_id, id DESC)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS system_alert_states (
            alert_key TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'NORMAL',
            severity TEXT NOT NULL DEFAULT 'warning',
            first_seen_at TEXT,
            last_changed_at TEXT,
            last_checked_at TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS system_alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key TEXT NOT NULL,
            severity TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_system_alert_events_created "
        "ON system_alert_events(created_at DESC, id DESC)"
    )


def _migration_0007_proxy_exclusive_allocation(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "proxy_profiles", "is_enabled", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "proxy_profiles", "paused_until", "TEXT")

    # Older releases allowed one proxy to be shared. Keep the oldest account
    # binding and safely detach the remaining bindings before adding the
    # database-level exclusive constraint.
    duplicate_rows = connection.execute(
        """
        SELECT proxy_profile_id
        FROM oci_accounts
        WHERE proxy_profile_id IS NOT NULL
        GROUP BY proxy_profile_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for duplicate in duplicate_rows:
        profile_id = int(duplicate["proxy_profile_id"])
        accounts = connection.execute(
            """
            SELECT id, custom_name
            FROM oci_accounts
            WHERE proxy_profile_id = ?
            ORDER BY datetime(created_at) ASC, id ASC
            """,
            (profile_id,),
        ).fetchall()
        kept = accounts[0]
        detached = accounts[1:]
        if detached:
            detached_ids = [int(row["id"]) for row in detached]
            placeholders = ",".join("?" for _ in detached_ids)
            connection.execute(
                f"""
                UPDATE oci_accounts
                SET proxy_enabled = 0,
                    proxy_profile_id = NULL,
                    proxy_url_encrypted = NULL,
                    proxy_label = NULL,
                    proxy_last_ip = NULL,
                    proxy_last_test_at = NULL,
                    proxy_last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                tuple(detached_ids),
            )
            connection.execute(
                """
                INSERT INTO audit_logs(
                    username, action, resource_type, resource_id, detail,
                    ip_address, created_at
                ) VALUES ('system', 'PROXY_EXCLUSIVE_MIGRATION',
                          'PROXY_PROFILE', ?, ?, NULL, CURRENT_TIMESTAMP)
                """,
                (
                    str(profile_id),
                    "kept_account_id=" + str(kept["id"]) +
                    ";detached_account_ids=" + ",".join(str(value) for value in detached_ids),
                ),
            )

    connection.execute("DROP INDEX IF EXISTS uq_oci_accounts_proxy_profile_exclusive")
    connection.execute(
        """
        CREATE UNIQUE INDEX uq_oci_accounts_proxy_profile_exclusive
        ON oci_accounts(proxy_profile_id)
        WHERE proxy_profile_id IS NOT NULL
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_proxy_profiles_enabled_health "
        "ON proxy_profiles(is_enabled, consecutive_failures, last_latency_ms)"
    )



MIGRATIONS = (
    Migration(1, "v1_baseline", "oci-nt-v1-baseline-20260731", _migration_0001_baseline),
    Migration(
        2,
        "manual_task_center_and_proxy_health",
        "oci-nt-v101-task-center-20260731",
        _migration_0002_task_center,
    ),
    Migration(
        3,
        "task_recovery_and_local_maintenance",
        "oci-nt-v101-task-recovery-maintenance-20260801",
        _migration_0003_task_recovery_and_maintenance,
    ),
    Migration(
        4,
        "v102_resilience_backup_schedule_and_release_tracking",
        "oci-nt-v102-resilience-release-20260801",
        _migration_0004_v102_resilience_and_release_tracking,
    ),
    Migration(
        5,
        "v103_recovery_sessions_and_resource_protection",
        "oci-nt-v103-recovery-sessions-resources-20260801",
        _migration_0005_v103_recovery_sessions_and_resource_protection,
    ),
    Migration(
        6,
        "v104_system_monitoring_and_alerts",
        "oci-nt-v104-system-monitoring-alerts-20260801",
        _migration_0006_v104_system_monitoring,
    ),
    Migration(
        7,
        "proxy_exclusive_allocation",
        "oci-nt-v105-proxy-exclusive-allocation-20260904",
        _migration_0007_proxy_exclusive_allocation,
    ),
)


def _ensure_tracking_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            version INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            execution_ms INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_version(singleton_id, version, updated_at) "
        "VALUES (1, 0, ?)",
        (utc_now(),),
    )
    connection.commit()


def run_schema_migrations(connection: sqlite3.Connection) -> int:
    _ensure_tracking_tables(connection)
    applied_rows = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    applied = {int(row["version"]): dict(row) for row in applied_rows}

    for migration in MIGRATIONS:
        existing = applied.get(migration.version)
        if existing:
            if existing["checksum"] != migration.checksum:
                raise RuntimeError(
                    f"数据库迁移 #{migration.version} 校验不一致；拒绝继续启动"
                )
            continue

        started = time.monotonic()
        try:
            connection.execute("BEGIN IMMEDIATE")
            migration.apply(connection)
            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
            applied_at = utc_now()
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at, execution_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    applied_at,
                    elapsed_ms,
                ),
            )
            connection.execute(
                "UPDATE schema_version SET version = ?, updated_at = ? "
                "WHERE singleton_id = 1",
                (migration.version, applied_at),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    row = connection.execute(
        "SELECT version FROM schema_version WHERE singleton_id = 1"
    ).fetchone()
    return int(row["version"] if row else 0)


def recover_interrupted_runtime_state(connection: sqlite3.Connection) -> None:
    now = utc_now()
    if _table_exists(connection, "bulk_check_jobs"):
        connection.execute(
            """
            UPDATE bulk_check_jobs
            SET status = 'INTERRUPTED', finished_at = ?,
                error = COALESCE(error, '服务重启，任务未自动恢复')
            WHERE status IN ('PENDING', 'RUNNING', 'WAITING')
            """,
            (now,),
        )
    if _table_exists(connection, "launch_jobs"):
        connection.execute(
            """
            UPDATE launch_jobs
            SET status = 'INTERRUPTED', finished_at = ?,
                last_error = COALESCE(last_error, '服务重启，任务未自动恢复')
            WHERE status IN ('PENDING', 'RUNNING', 'WAITING', 'CANCELLING')
            """,
            (now,),
        )
    if _table_exists(connection, "manual_tasks"):
        active_ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM manual_tasks
                WHERE status IN ('PENDING', 'RUNNING', 'WAITING', 'CANCELLING')
                """
            ).fetchall()
        ]
        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            connection.execute(
                f"""
                UPDATE manual_task_items
                SET status = 'INTERRUPTED', finished_at = ?, updated_at = ?,
                    error = COALESCE(error, '服务重启，项目未自动恢复'),
                    error_category = COALESCE(error_category, 'INTERRUPTED'),
                    retryable = 1, next_retry_at = NULL,
                    recovery_state = CASE
                        WHEN (SELECT task_type FROM manual_tasks task WHERE task.id = manual_task_items.task_id)
                             IN ('ACCOUNT_CHECK','PROXY_HEALTH') THEN 'SAFE_RETRY'
                        WHEN (SELECT task_type FROM manual_tasks task WHERE task.id = manual_task_items.task_id) = 'INSTANCE_BATCH'
                             AND json_extract((SELECT options_json FROM manual_tasks task WHERE task.id = manual_task_items.task_id), '$.operation') = 'SYNC_ACCOUNTS'
                             THEN 'SAFE_RETRY'
                        WHEN (SELECT task_type FROM manual_tasks task WHERE task.id = manual_task_items.task_id) = 'INSTANCE_BATCH'
                             AND json_extract((SELECT options_json FROM manual_tasks task WHERE task.id = manual_task_items.task_id), '$.operation') = 'REPLACE_PUBLIC_IP'
                             THEN 'MANUAL_ONLY'
                        WHEN (SELECT task_type FROM manual_tasks task WHERE task.id = manual_task_items.task_id) = 'INSTANCE_BATCH'
                             THEN 'STATE_CHECK_REQUIRED'
                        ELSE 'MANUAL_REVIEW'
                    END,
                    recovery_note = CASE
                        WHEN (SELECT task_type FROM manual_tasks task WHERE task.id = manual_task_items.task_id) = 'INSTANCE_BATCH'
                             AND json_extract((SELECT options_json FROM manual_tasks task WHERE task.id = manual_task_items.task_id), '$.operation') = 'REPLACE_PUBLIC_IP'
                             THEN '换公网 IP 结果未知，不允许自动补执行'
                        ELSE '服务重启后需要安全恢复检查'
                    END
                WHERE task_id IN ({placeholders})
                  AND status IN ('PENDING', 'RUNNING')
                """,
                (now, now, *active_ids),
            )
            connection.execute(
                f"""
                UPDATE manual_tasks
                SET status = 'INTERRUPTED', finished_at = ?, updated_at = ?,
                    current_item_key = NULL, current_item_name = NULL,
                    next_item_at = NULL,
                    error = COALESCE(error, '服务重启，任务未自动恢复'),
                    completed = (
                        SELECT COUNT(*) FROM manual_task_items item
                        WHERE item.task_id = manual_tasks.id
                          AND item.status IN ('SUCCEEDED','FAILED','SKIPPED','CANCELLED','INTERRUPTED')
                    ),
                    succeeded = (
                        SELECT COUNT(*) FROM manual_task_items item
                        WHERE item.task_id = manual_tasks.id AND item.status = 'SUCCEEDED'
                    ),
                    failed = (
                        SELECT COUNT(*) FROM manual_task_items item
                        WHERE item.task_id = manual_tasks.id AND item.status = 'FAILED'
                    ),
                    skipped = (
                        SELECT COUNT(*) FROM manual_task_items item
                        WHERE item.task_id = manual_tasks.id AND item.status = 'SKIPPED'
                    ),
                    interrupted = (
                        SELECT COUNT(*) FROM manual_task_items item
                        WHERE item.task_id = manual_tasks.id AND item.status = 'INTERRUPTED'
                    )
                WHERE id IN ({placeholders})
                """,
                (now, now, *active_ids),
            )
    if _table_exists(connection, "vnc_sessions"):
        connection.execute(
            """
            UPDATE vnc_sessions
            SET status = 'INTERRUPTED', stopped_at = ?,
                error = COALESCE(error, '服务重启，VNC 隧道未自动恢复')
            WHERE status IN ('STARTING', 'ACTIVE')
            """,
            (now,),
        )
    if _table_exists(connection, "launch_catalog_snapshots"):
        connection.execute(
            "DELETE FROM launch_catalog_snapshots "
            "WHERE created_at < datetime('now', '-1 day')"
        )
    connection.commit()
