from pathlib import Path


def test_instance_detail_dialog_has_bounded_filled_desktop_layout():
    root = Path(__file__).resolve().parents[2]
    css = (root / "frontend/styles.css").read_text(encoding="utf-8")
    assert "final-consolidation-r2 acceptance polish" in css
    assert "height: min(760px, calc(100dvh - 32px)) !important" in css
    assert "grid-template-rows: auto auto auto minmax(0, 1fr) auto !important" in css


def test_read_cache_coalescing_is_present():
    root = Path(__file__).resolve().parents[2]
    js = (root / "frontend/ui2.js").read_text(encoding="utf-8")
    assert "ntReadCacheMemo" in js
    assert "ntLegacyCacheProbeDone" in js
    assert "NT_READ_CACHE_MEMO_MS = 5000" in js


def test_monitor_disconnect_noise_is_suppressed_and_guard_is_listed():
    app = Path(__file__).resolve().parents[1] / "app"
    monitor = (app / "monitor_agent.py").read_text(encoding="utf-8")
    guard = (app / "docker_socket_guard.py").read_text(encoding="utf-8")
    assert '"oci-nt-docker-guard"' in monitor
    assert "except (BrokenPipeError, ConnectionResetError)" in monitor
    assert "except (BrokenPipeError, ConnectionResetError)" in guard


def test_upgrade_backup_is_host_side_and_verified():
    root = Path(__file__).resolve().parents[2]
    script = (root / "deploy/update-from-github.sh").read_text(encoding="utf-8")
    assert 'DB="$PROJECT/data/oci-nt.db"' in script
    assert 'BACKUP_DIR="$PROJECT/data/backups"' in script
    assert 'DB_BACKUP="$BACKUP_DIR/pre-git-update-${STAMP}.db"' in script
    assert "a.backup(b)" in script
    assert 'PRAGMA quick_check' in script
    assert "docker exec oci-nt-api python" not in script
