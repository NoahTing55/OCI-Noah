from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-id", default="")
    parser.add_argument("--fingerprint", default="")
    parser.add_argument("--previous-version", default="")
    parser.add_argument("--log-path", default="")
    parser.add_argument("--detail", default="{}")
    args = parser.parse_args()
    try:
        detail = json.loads(args.detail)
    except json.JSONDecodeError:
        detail = {"message": args.detail}

    connection = sqlite3.connect(args.db, timeout=30)
    try:
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
        row = connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id = 1"
        ).fetchone()
        schema_version = int(row[0] if row else 0)
        connection.execute(
            """
            INSERT INTO system_upgrade_history (
                version, build_id, source_fingerprint, previous_version,
                event_type, status, schema_version, log_path, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                args.version,
                args.build_id or None,
                args.fingerprint or None,
                args.previous_version or None,
                args.event.strip().upper(),
                args.status.strip().upper(),
                schema_version,
                args.log_path or None,
                json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
