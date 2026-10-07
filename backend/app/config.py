from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OCI-N&T"
    api_prefix: str = "/api/v1"
    version: str = "2.0.0"
    display_version: str = "N&T 2.0"

    data_dir: Path = Path("/app/data")
    db_path: Path = Path("/app/data/oci-nt.db")
    backup_dir: Path = Path("/app/data/backups")

    secret_key: str
    credential_encryption_key: str
    access_token_minutes: int = 1440

    admin_username: str = "admin"
    admin_password: str

    # Google OAuth is optional. Local username/password login always remains available.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None
    google_allowed_email: str | None = None
    google_frontend_return_url: str = "/"

    # User-triggered full-account checks only. No automatic scheduler exists; all OCI requests remain user-triggered.
    bulk_check_interval_seconds: int = 30
    candidate_mode: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
