from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_disposable_lifecycle_contract():
    s=(ROOT/"deploy/single/ci-smoke.sh").read_text()
    for marker in ("GITHUB_ACTIONS", "docker stop --time 35", "docker start", "PRAGMA journal_mode=WAL", "PRAGMA quick_check", "SINGLE_CONTAINER_PERSISTENCE_RESTART_OK", "SINGLE_CONTAINER_SERVICE_UID_OK", "SINGLE_CONTAINER_FAIL_FAST_OK"):
        assert marker in s
    assert "ci-persistence.db" in s
    assert "oci-nt.db" not in s
    assert "docker compose down" not in s

def test_no_production_or_host_data_read():
    s=(ROOT/"deploy/single/ci-smoke.sh").read_text()
    assert '"$dir/data:/app/data"' in s
    assert '"$dir/logs:/app/logs"' in s
    assert "ci-only-not-a-secret" in s
