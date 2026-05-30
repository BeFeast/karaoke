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

    # Clerk publishable key — NOT a secret; the SPA needs it to boot the
    # Clerk frontend SDK. Exposed via ``GET /config``. Empty disables the
    # Clerk sign-in UI (the SPA falls back to trusted-LAN "LAN mode").
    clerk_publishable_key: str = ""  # KARAOKE_CLERK_PUBLISHABLE_KEY
    # Gate: the SPA only switches to Clerk sign-in when this is true AND a
    # publishable key is set. Keeps the SPA in trusted-LAN mode until the
    # Clerk app's allowed origins are configured + smoke-tested (#12).
    clerk_spa_enabled: bool = False  # KARAOKE_CLERK_SPA_ENABLED

    # Default owner used by trusted-LAN / machine-bearer flows.
    default_owner_subject: str = ""
    default_owner_email: str = ""

    # Test-only escape hatch: skip Clerk JWT validation when set.
    auth_test_mode: bool = False

    # ---- SPA (Submitter single-page app) ----
    # Filesystem path to the built Vite SPA (``web/spa/dist``) inside the
    # container. Mounted at ``/app`` by ``create_app`` only when present, so
    # dev/test runs without a build still boot.
    spa_dist_path: str = "/app/web/spa/dist"

    # ---- Worker / vast.ai ----
    # The coordinator (devbox) downloads + normalizes locally, then ships the
    # working wav to an ephemeral vast.ai GPU instance for Demucs + Whisper.
    # The instance is ALWAYS destroyed in a ``finally`` clause; these caps are
    # the safety rails that keep a single job (and the rolling day) bounded.
    #
    # ``device_mode`` selects the dispatch path:
    #   - "auto"      → real vast.ai when a key is set, else mock (CI default).
    #   - "vast"      → force real vast.ai (errors if no key).
    #   - "cpu-local" → reserved for a future local-CPU fallback (not yet wired;
    #                   currently behaves like "vast" minus GPU expectations).
    #   - "mock"      → always the in-process mock worker.
    device_mode: str = "auto"  # auto | vast | cpu-local | mock

    vast_api_key: str = ""  # KARAOKE_VAST_API_KEY; empty + auto → mock path.
    vast_image: str = "ghcr.io/befeast/karaoke-vast:cuda12.4"

    # Offer-selection / budget tunables (mirror scribe naming). Overridable via
    # Infisical (KARAOKE_VAST_*).
    vast_max_price_per_hour: float = 2.0
    vast_max_job_cost: float = 0.35  # per-job USD ceiling; refuse/abort beyond.
    vast_min_cuda: float = 12.4  # never land on a host whose driver < CUDA 12.4.
    vast_instance_ready_timeout: int = 600  # per-attempt startup budget (s); 10GB image cold-start.
    vast_offer_attempts: int = 12  # distinct offers tried per job.
    # GPU model allowlist (mirror scribe's regex shape, narrowed to the cards
    # that comfortably run htdemucs + faster-whisper large-v3-turbo).
    vast_gpu_regex: str = (
        r"\b("
        r"RTX\s+4090|"
        r"(RTX\s+)?A[2456][05]00|A10|A40|"
        r"L4|L40S?|"
        r"RTX\s+(4000|4500|5000|5500|6000)(\s+Ada(\s+Generation)?)?"
        r")\b"
    )
    # Rolling 24-hour hard ceiling on vast spend (USD). 0 disables. When the
    # cap is reached the worker refuses to provision a new instance.
    vast_daily_cost_cap: float = 5.0

    # ---- RunPod Serverless (alternative GPU runtime; see issue #33) ----
    runpod_api_key: str = ""  # KARAOKE_RUNPOD_API_KEY; empty disables.
    runpod_endpoint_id: str = ""  # KARAOKE_RUNPOD_ENDPOINT_ID; created by scripts/runpod_provision.py.
    runpod_max_job_cost: float = 0.50  # per-job USD ceiling; cancel + raise beyond.
    runpod_daily_cost_cap: float = 5.0  # rolling 24h cap (shares Job.vast_cost_micros bookkeeping).
    runpod_poll_interval_s: float = 2.0
    runpod_request_timeout_s: int = 30
    runpod_wall_ceiling_s: float = 900.0  # max poll-loop wall clock per job.
    # Conservative \$/hr estimate for cost projection mid-poll. RunPod's
    # cheapest 16-24GB Flex is \$0.58-0.68/hr; we use 0.68 (pessimistic).
    runpod_hourly_rate_estimate: float = 0.68

    # ---- R2 (Cloudflare) for audio upload (RunPod /run has ~10MB body cap) ----
    r2_endpoint_url: str = ""  # KARAOKE_R2_ENDPOINT_URL
    r2_bucket: str = ""  # KARAOKE_R2_BUCKET
    r2_access_key_id: str = ""  # KARAOKE_R2_ACCESS_KEY_ID
    r2_secret_access_key: str = ""  # KARAOKE_R2_SECRET_ACCESS_KEY
    r2_presign_ttl_s: int = 600  # presigned-URL TTL — covers cold-start + work.

    # NFS-mounted artifact root inside the coordinator container.
    artifact_root: str = "/srv/artifacts"


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
