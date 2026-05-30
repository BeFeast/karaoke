"""Multi-layer authentication for the karaoke API.

Mirrors the scribe ``api/auth.py`` pattern (see project AGENTS.md):

- Trusted-LAN bypass for RFC1918 callers (machine-to-machine on the home LAN).
- Machine bearer (``KARAOKE_SERVICE_TOKEN``) for trusted services.
- Clerk JWT verified against a JWKS (cached in-process) with optional
  email allowlist.
- ``ktx_…`` extension tokens minted per Chrome-extension install, stored
  hashed (SHA-256) in the ``extension_tokens`` table.

Returns an :class:`Owner` (resolved identity) plus an :class:`AuthState`
tag describing how the request authenticated. Routes that need auth
should depend on :func:`require_owner`; routes open to the public should
not depend on auth at all.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import ipaddress
import json
import secrets
from dataclasses import dataclass
from enum import StrEnum

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import InvalidTokenError, PyJWK
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from karaoke.config import Settings, get_settings
from karaoke.db.models import ExtensionToken
from karaoke.db.session import get_session

EXTENSION_TOKEN_PREFIX = "ktx_"

# In-process JWKS cache, keyed by (source, value) so tests can swap inline JWKS.
_JWKS_CACHE: dict[tuple[str, str], dict] = {}


class AuthState(StrEnum):
    """How the request authenticated."""

    public = "public"
    trusted_lan = "trusted_lan"
    machine_bearer = "machine_bearer"
    clerk_user = "clerk_user"
    extension_token = "extension_token"


@dataclass(frozen=True)
class Owner:
    """Resolved owner identity attached to a request."""

    subject: str
    email: str | None = None
    display_name: str | None = None
    state: AuthState = AuthState.public


def token_hash(token: str) -> str:
    """SHA-256 hex of the raw token (used for ``ktx_…`` lookup)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_extension_token() -> str:
    """Mint a fresh ``ktx_…`` token (call sites store the SHA-256 hash)."""
    return f"{EXTENSION_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _bearer_token(request: Request, *, strict: bool = False) -> str | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        if strict:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid authorization header",
            )
        return None
    return parts[1]


def _trusted_networks(settings: Settings) -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for raw in settings.trusted_cidrs.split(","):
        value = raw.strip()
        if not value:
            continue
        networks.append(ipaddress.ip_network(value, strict=False))
    return networks


def _is_trusted_host(host: str, settings: Settings) -> bool:
    networks = _trusted_networks(settings)
    if not networks:
        # No trusted networks configured — LAN-trust is disabled entirely.
        return False
    if host == "testclient":
        # Starlette TestClient — trusted only when LAN-trust is enabled.
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def _client_ip(request: Request, settings: Settings) -> str:
    host = request.client.host if request.client else ""
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded_for and _is_trusted_host(host, settings):
        return forwarded_for
    return host


def is_trusted_lan_request(request: Request, settings: Settings) -> bool:
    return _is_trusted_host(_client_ip(request, settings), settings)


def _normal_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _allowed_emails(settings: Settings) -> frozenset[str]:
    return frozenset(
        email.strip().lower()
        for email in settings.auth_allowed_emails.replace("\n", ",").split(",")
        if email.strip()
    )


def _claim_str(claims: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _claims_email(claims: dict[str, object]) -> str | None:
    return _normal_email(
        _claim_str(claims, "email", "primary_email", "primary_email_address", "email_address")
    )


def _claims_name(claims: dict[str, object]) -> str | None:
    name = _claim_str(claims, "name", "full_name", "display_name", "username")
    if name:
        return name
    first = claims.get("given_name")
    last = claims.get("family_name")
    joined = " ".join(
        part.strip() for part in (first, last) if isinstance(part, str) and part.strip()
    )
    return joined or None


def _default_owner(settings: Settings, *, state: AuthState) -> Owner:
    subject = settings.default_owner_subject.strip() or settings.default_owner_email.strip()
    email = settings.default_owner_email.strip() or None
    if not subject:
        # Trusted callers without a configured default owner still get a
        # synthetic identity so jobs can be attributed.
        subject = f"karaoke:{state.value}"
    return Owner(subject=subject, email=email, display_name=None, state=state)


# ---------------------------------------------------------------------------
# Clerk JWT verification (cached JWKS)
# ---------------------------------------------------------------------------


def _clerk_configured(settings: Settings) -> bool:
    return bool(settings.clerk_issuer.strip()) and (
        bool(settings.clerk_jwks_url.strip()) or bool(settings.clerk_jwks_json.strip())
    )


def _load_jwks(settings: Settings) -> dict:
    inline = settings.clerk_jwks_json.strip()
    if inline:
        cache_key = ("inline", inline)
        if cache_key not in _JWKS_CACHE:
            try:
                _JWKS_CACHE[cache_key] = json.loads(inline)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Clerk JWKS JSON is invalid",
                ) from exc
        return _JWKS_CACHE[cache_key]

    url = settings.clerk_jwks_url.strip()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Clerk JWKS is not configured",
        )
    cache_key = ("url", url)
    if cache_key not in _JWKS_CACHE:
        try:
            response = httpx.get(url, timeout=5.0)
            response.raise_for_status()
            _JWKS_CACHE[cache_key] = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Clerk JWKS fetch failed",
            ) from exc
    return _JWKS_CACHE[cache_key]


def _jwk_for_token(token: str, settings: Settings) -> PyJWK:
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid Clerk JWT",
        ) from exc

    kid = header.get("kid")
    keys = _load_jwks(settings).get("keys", [])
    if not isinstance(keys, list):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Clerk JWKS is invalid",
        )

    key = next(
        (item for item in keys if isinstance(item, dict) and item.get("kid") == kid),
        None,
    )
    if key is None and kid is None and len(keys) == 1 and isinstance(keys[0], dict):
        key = keys[0]
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid Clerk JWT",
        )

    try:
        return PyJWK.from_dict(key)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Clerk JWKS is invalid",
        ) from exc


def _jwt_payload_unverified(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        decoded = json.loads(raw)
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _validate_clerk_jwt(token: str, settings: Settings) -> dict[str, object]:
    if settings.auth_test_mode:
        # Test escape hatch: trust unverified payload.
        return _jwt_payload_unverified(token)

    issuer = settings.clerk_issuer.strip()
    if not issuer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Clerk issuer is not configured",
        )
    jwk = _jwk_for_token(token, settings)
    try:
        claims = jwt.decode(
            token,
            key=jwk.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["exp"], "verify_aud": False},
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid Clerk JWT",
        ) from exc
    if not isinstance(claims, dict):
        return {}
    allowed = _allowed_emails(settings)
    if allowed:
        email = _claims_email(claims)
        if email is None or email not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="email is not allowed",
            )
    return claims


def _owner_from_clerk_claims(claims: dict[str, object]) -> Owner | None:
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        return None
    return Owner(
        subject=subject,
        email=_claims_email(claims),
        display_name=_claims_name(claims),
        state=AuthState.clerk_user,
    )


# ---------------------------------------------------------------------------
# Extension token lookup
# ---------------------------------------------------------------------------


async def _owner_from_extension_token(
    session: AsyncSession,
    token: str,
) -> Owner | None:
    if not token.startswith(EXTENSION_TOKEN_PREFIX):
        return None
    row = (
        await session.execute(
            select(ExtensionToken).where(ExtensionToken.token_hash == token_hash(token))
        )
    ).scalar_one_or_none()
    if row is None or row.disabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid extension token",
        )
    row.last_used_at = dt.datetime.now(dt.UTC)
    await session.commit()
    return Owner(
        subject=row.owner_subject,
        email=row.owner_email,
        display_name=row.owner_display_name,
        state=AuthState.extension_token,
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def resolve_owner(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Owner | None:
    """Resolve the request to an :class:`Owner` (or ``None`` if anonymous).

    Order of precedence:
    1. ``Authorization: Bearer …`` header — service token, ``ktx_`` extension
       token, or Clerk JWT (in that order).
    2. Trusted-LAN bypass (RFC1918 + 127.0.0.0/8 + ``testclient``).
    3. Anonymous (``None``).
    """
    bearer = _bearer_token(request, strict=True)
    if bearer is not None:
        # 1a. Machine bearer.
        machine = settings.service_token.strip()
        if machine and secrets.compare_digest(bearer, machine):
            return _default_owner(settings, state=AuthState.machine_bearer)

        # 1b. Extension token (ktx_… prefix shortcuts the Clerk path).
        if bearer.startswith(EXTENSION_TOKEN_PREFIX):
            ext_owner = await _owner_from_extension_token(session, bearer)
            if ext_owner is not None:
                return ext_owner

        # 1c. Clerk JWT.
        if _clerk_configured(settings) or settings.auth_test_mode:
            claims = _validate_clerk_jwt(bearer, settings)
            owner = _owner_from_clerk_claims(claims)
            if owner is not None:
                return owner

        # Bearer was supplied but matched no scheme — refuse explicitly so we
        # never silently fall back to LAN-trust on a malformed header.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unrecognised bearer token",
        )

    # 2. Trusted-LAN bypass.
    if is_trusted_lan_request(request, settings):
        return _default_owner(settings, state=AuthState.trusted_lan)

    # 3. Anonymous.
    return None


async def require_owner(
    owner: Owner | None = Depends(resolve_owner),
) -> Owner:
    """Reject anonymous requests with 401."""
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return owner
