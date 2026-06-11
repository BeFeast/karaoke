"""Self-service API for ``ktx_`` extension tokens (issue #133).

Mint / list / revoke over the existing ``extension_tokens`` table, reusing
``new_extension_token()`` / ``token_hash()`` from :mod:`karaoke.api.auth`.

Mint is restricted to ``clerk_user`` and ``machine_bearer``: an
``extension_token`` actor carries privileges trusted-LAN deliberately does
not (the #73 cookie-writer precedent kept LAN-anon out of cookie writes), so
LAN-anon mint would be a privilege escalation. An ``extension_token`` actor
cannot mint either (no token-begets-token); ``machine_bearer`` mint replaces
the manual DB-insert operator path.

The raw ``ktx_…`` value appears exactly once, in the mint response; only its
SHA-256 (``token_hash``) is persisted and the raw value is never logged.
"""
from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from karaoke.api.auth import (
    AuthState,
    Owner,
    new_extension_token,
    require_owner,
    token_hash,
)
from karaoke.db.models import ExtensionToken
from karaoke.db.session import get_session

_log = logging.getLogger(__name__)

TOKEN_MINTER_STATES = {AuthState.clerk_user, AuthState.machine_bearer}

# Same admin model as /jobs (`_can_owner_view`): trusted-LAN and machine
# bearer see and may revoke every row.
_ADMIN_STATES = {AuthState.trusted_lan, AuthState.machine_bearer}

DEFAULT_LABEL = "Chrome extension"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ExtensionTokenCreate(BaseModel):
    """Body for ``POST /tokens``."""

    label: str | None = Field(default=None, max_length=255)


class ExtensionTokenMinted(BaseModel):
    """Mint response — the only place the raw ``ktx_…`` value ever appears."""

    id: int
    token: str
    label: str
    created_at: dt.datetime


class ExtensionTokenOut(BaseModel):
    """Owner-visible token row — never the hash, never the raw value."""

    id: int
    label: str | None
    disabled: bool
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    owner_subject: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


router = APIRouter()


async def require_token_minter(owner: Owner = Depends(require_owner)) -> Owner:
    """Restrict token minting to a signed-in Clerk user or the machine bearer."""
    if owner.state not in TOKEN_MINTER_STATES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="token mint requires a signed-in user or machine bearer",
        )
    return owner


@router.post(
    "/tokens",
    response_model=ExtensionTokenMinted,
    status_code=status.HTTP_201_CREATED,
    tags=["tokens"],
)
async def mint_token(
    payload: ExtensionTokenCreate | None = None,
    owner: Owner = Depends(require_token_minter),
    session: AsyncSession = Depends(get_session),
) -> ExtensionTokenMinted:
    """Mint a fresh ``ktx_…`` token for the caller.

    The raw value is returned once and never persisted — only the SHA-256
    hash is stored, so a lost token can only be revoked and re-minted.
    """
    raw = new_extension_token()
    label = ((payload.label if payload else None) or "").strip() or DEFAULT_LABEL
    row = ExtensionToken(
        token_hash=token_hash(raw),
        owner_subject=owner.subject,
        owner_email=owner.email,
        owner_display_name=owner.display_name,
        label=label,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    # id + actor state only — the raw token value must never reach the logs.
    _log.info("extension token minted: id=%d by %s", row.id, owner.state.value)
    return ExtensionTokenMinted(
        id=row.id,
        token=raw,
        label=label,
        created_at=row.created_at,
    )


@router.get("/tokens", response_model=list[ExtensionTokenOut], tags=["tokens"])
async def list_tokens(
    owner: Owner = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> list[ExtensionTokenOut]:
    """List the caller's tokens, newest first; admin callers see every row."""
    stmt = select(ExtensionToken).order_by(
        ExtensionToken.created_at.desc(), ExtensionToken.id.desc()
    )
    if owner.state not in _ADMIN_STATES:
        stmt = stmt.where(ExtensionToken.owner_subject == owner.subject)
    rows = (await session.scalars(stmt)).all()
    return [
        ExtensionTokenOut(
            id=row.id,
            label=row.label,
            disabled=row.disabled,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            owner_subject=row.owner_subject,
        )
        for row in rows
    ]


@router.delete(
    "/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["tokens"],
)
async def revoke_token(
    token_id: int,
    owner: Owner = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-revoke a token: ``disabled=True``, audit row kept.

    Cross-owner revoke returns 404 to hide existence (same as /jobs); admin
    may revoke any row; re-revoking an already-disabled token is a 204 no-op.
    auth.py already rejects disabled tokens with 401 on their next use.
    """
    row = await session.get(ExtensionToken, token_id)
    if row is None or (
        owner.state not in _ADMIN_STATES and row.owner_subject != owner.subject
    ):
        raise HTTPException(status_code=404, detail="token not found")
    if not row.disabled:
        row.disabled = True
        await session.commit()
        _log.info("extension token revoked: id=%d by %s", token_id, owner.state.value)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
