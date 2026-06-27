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

    # When true, this Cove process runs as a zone agent (no SPA/login/admin; only
    # the mTLS Docker exposure + agent API). Set via COVE_AGENT_MODE on agents.
    agent_mode: bool = False

    # Dedicated signing key for per-workspace stream tokens, kept separate from
    # the app secret (which signs session/refresh/access tokens). This is the ONLY
    # signing key shared with zone agents (provisioned at enrollment) so an agent's
    # Traefik can verify stream tokens locally without ever holding the app secret —
    # minting a stream token only grants access to a workspace the agent already
    # runs, so it is a bounded capability. On the control plane it is generated and
    # persisted as a file (like secret.key); on an agent it is provided via
    # COVE_STREAM_SIGNING_KEY. (empty -> None)
    stream_signing_key: Optional[str] = None
    # Path, inside the Traefik container, where the control plane's per-zone mTLS
    # client certs are mounted (used by the HTTP dynamic-config serversTransports).
    zone_certs_mount: str = "/zone-certs"
    # Container image a zone agent runs (same Cove image, started with
    # COVE_AGENT_MODE=1). Baked into the generated install script.
    zone_agent_image: str = "cove:local"
    # On a zone agent: the LOCAL docker-socket-proxy the Docker reverse-proxy
    # forwards to (the Docker daemon is never exposed on a network port).
    agent_docker_socket_url: str = "http://cove-agent-sockproxy:2375"
    # On a zone agent: the only client-cert CN allowed to reach the agent API +
    # Docker proxy (its control plane's per-zone cert, cove-cp-<public_id>).
    # Enforced via Traefik's passTLSClientCert when set; unset disables the check.
    # (empty -> None)
    agent_expected_client_cn: Optional[str] = None

    # Token lifetimes
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    # Per-workspace stream token lifetime (subdomain mode). The stream cookie is
    # scoped to a single workspace origin; this is how long a stream stays
    # authenticated before the SPA must mint a fresh token.
    stream_token_minutes: int = 480
    # How long a zone enrollment token stays valid. Generous (default 1h) because
    # a fresh node may need to install Docker before it can enroll; single-use, so
    # the window only bounds how long a leaked token is replayable.
    zone_enroll_token_minutes: int = 60
    # Lifetime of the one-time bootstrap token carried in the ``?__cove_t`` URL.
    # Kept short because it rides in a URL (logs/Referer/history): it is consumed
    # exactly once to set the stream cookie, then useless. Distinct from the
    # cookie token above, which is minted fresh on a successful bootstrap.
    stream_bootstrap_minutes: int = 5

    # Cookie settings
    cookie_secure: bool = True
    cookie_session_name: str = "cove_session"
    cookie_refresh_name: str = "cove_refresh"
    # Per-workspace stream auth cookie (subdomain mode). Set host-only on the
    # workspace origin so the powerful session cookie never reaches it.
    cookie_stream_name: str = "cove_stream"

    # When set, workspace streams route at their own origin
    # ``{public_id}.{workspace_domain}`` (subdomain mode). When unset, streams
    # keep the subpath route ``/workspace/{public_id}/`` (empty -> None).
    workspace_domain: Optional[str] = None

    # Public origin of the SPA (e.g. https://cove.example.com). In subdomain
    # mode the workspace iframe is cross-origin, so its responses must allow
    # framing from this origin via CSP frame-ancestors (empty -> None -> 'self').
    app_origin: Optional[str] = None

    traefik_network: str = "cove-net"
    traefik_container: str = "cove-traefik"
    # Internal authority Traefik's ForwardAuth uses to reach this app (the host
    # of the forwardauth `address`, e.g. "cove:8080"). When set, /api/auth/forward
    # rejects any request whose Host header isn't this value — a public request
    # routed through Traefik arrives with the public Host, so it can't reach the
    # endpoint. Unset (dev/tests) = no host enforcement. (empty -> None)
    forward_auth_host: Optional[str] = None
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

    @field_validator(
        "storage_path",
        "db_encryption_key",
        "workspace_domain",
        "app_origin",
        "forward_auth_host",
        "stream_signing_key",
        "agent_expected_client_cn",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        return None if v == "" else v

    @property
    def subdomain_routing_enabled(self) -> bool:
        """True when per-workspace subdomain routing is enabled."""
        return bool(self.workspace_domain)

    def workspace_host(self, public_id: str) -> str:
        """The host a workspace stream is served at in subdomain mode."""
        return f"{public_id}.{self.workspace_domain}"

    # OIDC — all optional; OIDC is disabled when issuer is unset
    oidc_issuer: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_client_secret: Optional[str] = None
    oidc_scopes: str = "openid email profile groups"
    oidc_admin_group: Optional[str] = None
    oidc_provider_name: str = "SSO"
    # When true (and OIDC is configured), local username/password login + setup
    # are disabled and the SPA goes straight to the IdP. Gated on oidc_enabled so
    # a half-config can't lock everyone out — set COVE_OIDC_ONLY=false to recover.
    oidc_only: bool = False

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    @property
    def oidc_only_active(self) -> bool:
        return self.oidc_only and self.oidc_enabled

    @property
    def secret_key_file(self) -> Path:
        return self.secret_key_path or (self.data_dir / "secret.key")

    # Private-CA material for zone mTLS (generated on first use, like secret.key).
    @property
    def ca_cert_file(self) -> Path:
        return self.data_dir / "ca" / "ca.crt"

    @property
    def ca_key_file(self) -> Path:
        return self.data_dir / "ca" / "ca.key"

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

    def get_stream_signing_key(self) -> str:
        """The stream-token signing key. On an agent this is provided via
        COVE_STREAM_SIGNING_KEY; on the control plane it is generated/persisted
        as ``data_dir/stream.key`` (0600), mirroring get_secret_key."""
        if self.stream_signing_key:
            return self.stream_signing_key
        import os

        path = self.data_dir / "stream.key"
        if path.exists():
            return path.read_text().strip()
        import secrets
        key = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key)
        os.chmod(path, 0o600)
        return key

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir}/cove.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
