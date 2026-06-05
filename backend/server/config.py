from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COVE_", env_file=".env", extra="ignore")

    data_dir: Path = Path("/app/data")
    secret_key_path: Optional[Path] = None  # defaults to data_dir/secret.key
    jwt_algorithm: str = "HS256"

    # Token lifetimes
    access_token_minutes: int = 30
    refresh_token_days: int = 7

    # Cookie settings
    cookie_secure: bool = True
    cookie_session_name: str = "cove_session"
    cookie_refresh_name: str = "cove_refresh"

    traefik_network: str = "cove-net"
    traefik_container: str = "cove-traefik"
    workspace_puid: int = 1000
    workspace_pgid: int = 1000
    workspace_tz: str = "UTC"
    storage_path: Optional[Path] = None  # COVE_STORAGE_PATH — bind-mount root for workspace homes

    # Login rate limiting
    login_rate_limit: int = 10
    login_rate_window_seconds: int = 60

    # Maximum size (MiB) for a single file upload.
    max_upload_mb: int = 1024

    # Optional SQLCipher database encryption key (empty -> None)
    db_encryption_key: Optional[str] = None

    @field_validator("storage_path", "db_encryption_key", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        return None if v == "" else v

    # OIDC — all optional; OIDC is disabled when issuer is unset
    oidc_issuer: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_client_secret: Optional[str] = None
    oidc_scopes: str = "openid email profile groups"
    oidc_admin_group: Optional[str] = None
    oidc_provider_name: str = "SSO"

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    @property
    def secret_key_file(self) -> Path:
        return self.secret_key_path or (self.data_dir / "secret.key")

    def get_secret_key(self) -> str:
        import os

        path = self.secret_key_file
        if path.exists():
            return path.read_text().strip()
        import secrets
        key = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key)
        # Restrict to owner read/write only — the secret key signs all tokens.
        os.chmod(path, 0o600)
        return key

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir}/cove.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
