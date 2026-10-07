from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_iam_user_lifecycle_contract():
    router = (ROOT / "backend/app/resource_router.py").read_text()
    service = (ROOT / "backend/app/oci_rc_service.py").read_text()
    frontend = (ROOT / "frontend/rc.js").read_text()
    html = (ROOT / "frontend/index.html").read_text()
    assert "/notification-email" in router
    assert "/reset-mfa" in router
    assert "UpdateUserDetails(email=email.strip())" in service
    assert "list_mfa_totp_devices" in service
    assert "delete_mfa_totp_device" in service
    assert 'description=(description or "").strip()' in service
    assert "iam-user-dialog" in html
    assert "iam-email-dialog" in html
    assert "data-rc-iam-mfa" in frontend
    assert "create_console_password" in frontend
