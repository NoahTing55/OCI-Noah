import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import settings
from .settings_repository import load_backup_policy


def create_sqlite_backup(label: str | None = None) -> dict:
    from .system_resource_service import assert_backup_resources

    assert_backup_resources(settings.db_path.stat().st_size if settings.db_path.exists() else 0)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    safe_label = "".join(ch for ch in (label or "manual") if ch.isalnum() or ch in ("-", "_"))[:40] or "manual"
    target = settings.backup_dir / f"oci-nt-{safe_label}-{stamp}.db"
    source = sqlite3.connect(settings.db_path, timeout=30)
    try:
        backup = sqlite3.connect(target, timeout=30)
        try:
            source.backup(backup)
        finally:
            backup.close()
    finally:
        source.close()
    verification = verify_sqlite_backup(target.name)
    if not verification["ok"]:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"备份完整性检查失败：{verification['quick_check']}")
    cleanup = cleanup_old_backups()
    return {**backup_info(target), **verification, "cleanup": cleanup}


def backup_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "size": stat.st_size,
        "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _backup_target(name: str) -> Path:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    value = str(name or "").strip()
    if (
        not value
        or Path(value).name != value
        or not value.startswith("oci-nt-")
        or not value.endswith(".db")
    ):
        raise ValueError("无效的备份文件名")
    backup_root = settings.backup_dir.resolve()
    target = settings.backup_dir / value
    resolved = target.resolve(strict=False)
    if resolved.parent != backup_root or target.is_symlink():
        raise ValueError("无效的备份文件路径")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(value)
    return target


def resolve_backup_path(name: str) -> Path:
    return _backup_target(name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sqlite_backup(name: str) -> dict:
    target = _backup_target(name)
    connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=30)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        quick_check = str(row[0] if row else "unknown")
    finally:
        connection.close()
    return {
        "ok": quick_check.lower() == "ok",
        "quick_check": quick_check,
        "sha256": _sha256(target),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def maintain_sqlite_database(*, vacuum: bool = False) -> dict:
    db_path = settings.db_path
    before = db_path.stat().st_size if db_path.exists() else 0
    wal_path = Path(str(db_path) + "-wal")
    wal_before = wal_path.stat().st_size if wal_path.exists() else 0
    connection = sqlite3.connect(db_path, timeout=60, isolation_level=None)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        quick_before = str(row[0] if row else "unknown")
        if quick_before.lower() != "ok":
            raise RuntimeError(f"数据库完整性检查失败：{quick_before}")
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        connection.execute("PRAGMA optimize")
        if vacuum:
            connection.execute("VACUUM")
        row = connection.execute("PRAGMA quick_check").fetchone()
        quick_after = str(row[0] if row else "unknown")
        if quick_after.lower() != "ok":
            raise RuntimeError(f"数据库维护后完整性检查失败：{quick_after}")
    finally:
        connection.close()
    after = db_path.stat().st_size if db_path.exists() else 0
    wal_after = wal_path.stat().st_size if wal_path.exists() else 0
    return {
        "ok": True,
        "quick_check_before": quick_before,
        "quick_check_after": quick_after,
        "vacuum": bool(vacuum),
        "database_bytes_before": before,
        "database_bytes_after": after,
        "wal_bytes_before": wal_before,
        "wal_bytes_after": wal_after,
        "checkpoint": list(checkpoint or ()),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def list_backups() -> list[dict]:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    backups = [backup_info(path) for path in settings.backup_dir.glob("oci-nt-*.db")]
    return sorted(backups, key=lambda item: item["created_at"], reverse=True)


def delete_sqlite_backup(name: str) -> dict:
    target = _backup_target(name)
    info = backup_info(target)
    target.unlink()
    return info


def cleanup_old_backups(
    retention_days: int | None = None,
    keep_latest: int | None = None,
    max_count: int | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    policy = load_backup_policy()
    days = int(retention_days if retention_days is not None else policy["retention_days"])
    keep = int(keep_latest if keep_latest is not None else policy["keep_latest"])
    maximum = int(max_count if max_count is not None else policy["max_count"])
    days = max(1, min(days, 3650))
    keep = max(1, min(keep, 1000))
    maximum = max(keep, min(maximum, 5000))

    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(
        (path for path in settings.backup_dir.glob("oci-nt-*.db") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    candidates: list[dict] = []
    for index, path in enumerate(paths):
        if index < keep:
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        reasons: list[str] = []
        if modified < cutoff:
            reasons.append("超过保留天数")
        if index >= maximum:
            reasons.append("超过最大份数")
        if reasons:
            info = backup_info(path)
            info["reason"] = "、".join(reasons)
            candidates.append(info)

    deleted: list[dict] = []
    if not dry_run:
        for item in candidates:
            target = settings.backup_dir / item["name"]
            try:
                os.remove(target)
                deleted.append(item)
            except FileNotFoundError:
                continue

    remaining_count = len(paths) - (len(deleted) if not dry_run else 0)
    return {
        "dry_run": bool(dry_run),
        "retention_days": days,
        "keep_latest": keep,
        "max_count": maximum,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "deleted": deleted,
        "deleted_count": len(deleted),
        "remaining_count": remaining_count,
        "cutoff": cutoff.isoformat(),
    }

# Local backup deletion service.
