from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_single_typography_policy_and_dashboard_regular_weight():
    css = (ROOT / "frontend/ui-theme.css").read_text()
    assert css.count("/* OCI-N&T unified typography") == 1
    assert "/* OCI-N&T readable typography:" not in css
    assert "/* 2026-10 accessibility:" not in css
    policy = css.split("/* OCI-N&T unified typography", 1)[1]
    for item in (
        "--nt-font-body: 14px", "--nt-font-note: 13px",
        ".dashboard-account-row", ".dashboard-list-row",
        ".account-main", ".account-cell", ".api-card",
        ".launch-job-card", ".launch-presets",
        ".launch-table-all-tenants th", ".sidebar .nav",
        "font-weight: 400 !important", "font-weight: var(--nt-font-heading)",
    ):
        assert item in policy
