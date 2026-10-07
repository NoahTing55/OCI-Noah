from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_google_settings_and_oauth_security_contract():
    main = (ROOT / "backend/app/main.py").read_text()
    service = (ROOT / "backend/app/google_oauth_service.py").read_text()
    database = (ROOT / "backend/app/database.py").read_text()
    settings = (ROOT / "backend/app/settings_repository.py").read_text()
    assert "/settings/google-auth" in main
    assert "oauth_login_states" in database
    assert "consume_oauth_login_state" in service
    assert 'claims.get("nonce")' in service
    assert "email_verified" in service
    assert "encrypt_secret" in settings
    assert "GOOGLE_CLIENT_SECRET_KEY" in settings
