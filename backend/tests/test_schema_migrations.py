from __future__ import annotations

import sqlite3

from app.config import settings
from app.database import init_database
from app.schema_migrations import MIGRATIONS, run_schema_migrations


def _use_temp_database(tmp_path) -> None:
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "oci-nt.db"
    settings.backup_dir = tmp_path / "backups"


def test_migrations_are_ordered_and_idempotent(tmp_path):
    _use_temp_database(tmp_path)
    init_database()
    init_database()

    with sqlite3.connect(settings.db_path) as connection:
        version = connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id = 1"
        ).fetchone()[0]
        migrations = connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]

    assert version == MIGRATIONS[-1].version
    assert [row[0] for row in migrations] == [item.version for item in MIGRATIONS]
    assert quick_check == "ok"


def test_migration_checksum_mismatch_stops_startup(tmp_path):
    _use_temp_database(tmp_path)
    init_database()
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1"
        )
        connection.commit()
        connection.row_factory = sqlite3.Row
        try:
            run_schema_migrations(connection)
        except RuntimeError as exc:
            assert "校验不一致" in str(exc)
        else:
            raise AssertionError("checksum mismatch should stop startup")


def test_existing_v1_rows_survive_upgrade(tmp_path):
    _use_temp_database(tmp_path)
    connection = sqlite3.connect(settings.db_path)
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE proxy_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            proxy_url_encrypted TEXT NOT NULL,
            scheme TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL,
            has_auth INTEGER NOT NULL DEFAULT 0,
            last_ip TEXT,
            last_test_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE oci_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            custom_name TEXT NOT NULL,
            tenancy_ocid TEXT NOT NULL,
            user_ocid TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            region TEXT NOT NULL,
            private_key_encrypted TEXT NOT NULL,
            passphrase_encrypted TEXT,
            email TEXT,
            user_name TEXT,
            user_lifecycle_state TEXT,
            user_time_created TEXT,
            tenancy_name TEXT,
            home_region_key TEXT,
            account_status TEXT NOT NULL DEFAULT 'UNKNOWN',
            last_error TEXT,
            last_checked_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tenancy_ocid, user_ocid, fingerprint)
        );
        CREATE TABLE cloudflare_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            api_token_encrypted TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO users(username, password_hash) VALUES ('Noah', 'hash');
        INSERT INTO proxy_profiles(name, proxy_url_encrypted, scheme, host, port)
        VALUES ('旧代理', 'cipher', 'http', '127.0.0.1', 8080);
        INSERT INTO oci_accounts(
            custom_name, tenancy_ocid, user_ocid, fingerprint, region,
            private_key_encrypted, email, account_status
        ) VALUES (
            '旧租户', 'ocid1.tenancy.legacy', 'ocid1.user.legacy', 'aa:bb',
            'uk-london-1', 'cipher-key', 'legacy@example.com', 'ALIVE'
        );
        """
    )
    connection.commit()
    connection.close()

    init_database()

    with sqlite3.connect(settings.db_path) as upgraded:
        assert upgraded.execute("SELECT username FROM users").fetchone()[0] == "Noah"
        assert upgraded.execute("SELECT custom_name FROM oci_accounts").fetchone()[0] == "旧租户"
        proxy_columns = {
            row[1] for row in upgraded.execute("PRAGMA table_info(proxy_profiles)")
        }
        account_columns = {
            row[1] for row in upgraded.execute("PRAGMA table_info(oci_accounts)")
        }
        assert {"last_latency_ms", "last_success_at", "consecutive_failures"} <= proxy_columns
        assert "proxy_profile_id" in account_columns
        assert upgraded.execute(
            "SELECT version FROM schema_version WHERE singleton_id = 1"
        ).fetchone()[0] == 7


def test_restart_recovery_marks_pending_items_interrupted(tmp_path):
    _use_temp_database(tmp_path)
    init_database()
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute(
            """
            INSERT INTO manual_tasks(
                task_type, title, status, requested_by, total,
                options_json, summary_json, created_at, updated_at
            ) VALUES ('ACCOUNT_CHECK', '运行中任务', 'RUNNING', 'tester', 2, '{}', '{}', ?, ?)
            """,
            ('2026-07-31T00:00:00+00:00', '2026-07-31T00:00:00+00:00'),
        )
        task_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        for key, status in [('1', 'RUNNING'), ('2', 'PENDING')]:
            connection.execute(
                """
                INSERT INTO manual_task_items(
                    task_id, item_key, item_name, item_type, status,
                    payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'OCI_ACCOUNT', ?, '{}', '{}', ?, ?)
                """,
                (task_id, key, key, status, '2026-07-31T00:00:00+00:00', '2026-07-31T00:00:00+00:00'),
            )
        connection.commit()

    init_database()

    with sqlite3.connect(settings.db_path) as connection:
        connection.row_factory = sqlite3.Row
        task = connection.execute(
            "SELECT status, completed, interrupted FROM manual_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        item_statuses = [
            row[0] for row in connection.execute(
                "SELECT status FROM manual_task_items WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]
    assert task["status"] == "INTERRUPTED"
    assert task["completed"] == 2
    assert task["interrupted"] == 2
    assert item_statuses == ["INTERRUPTED", "INTERRUPTED"]
