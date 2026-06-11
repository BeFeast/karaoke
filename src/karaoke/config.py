"""Karaoke runtime settings.

Settings are sourced from environment variables (and optional ``.env``) via
``pydantic-settings``. Secrets read from Infisical at process start time are
expected to be exported into the environment by the runner before the app
starts; we never read Infisical inline.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Karaoke coordinator settings."""

    model_config = SettingsConfigDict(
        env_prefix="KARAOKE_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    # Database
    database_url: str = "sqlite+aiosqlite:///./karaoke.db"

    # HTTP / CORS
    cors_origins: str = ""  # comma-separated; empty = none

    # Public base URL used when constructing share links.
    public_base_url: str = "http://localhost:13140"

    # Filesystem root for produced artifacts. The HTML web UI serves files from
    # ``<artifacts_dir>/<job_token>/<relative_path>`` via /artifacts/{token}/{f}
    # (owner-aware). In production this is the TrueNAS NFS mount; tests use a
    # tmp dir.
    artifacts_dir: str = "./artifacts"

    # ---- Auth: machine bearer ----
    service_token: str = ""  # KARAOKE_SERVICE_TOKEN; empty disables.

    # ---- Auth: trusted LAN ----
    trusted_cidrs: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8"

    # ---- Auth: Clerk ----
    clerk_issuer: str = ""
    clerk_jwks_url: str = ""
    clerk_jwks_json: str = ""  # inline JWKS (tests / offline)
    clerk_secret_key: str = ""  # for Clerk Backend API user lookup
    clerk_backend_api_url: str = "https://api.clerk.com"
    auth_allowed_emails: str = ""  # comma-separated; empty = no allowlist

    # Default owner used by trusted-LAN / machine-bearer flows.
    default_owner_subject: str = ""
    default_owner_email: str = ""

    # Test-only escape hatch: skip Clerk JWT validation when set.
    auth_test_mode: bool = False


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings`."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_for_tests() -> None:
    """Drop the cached settings (tests mutate env between cases)."""
    global _settings
    _settings = None
