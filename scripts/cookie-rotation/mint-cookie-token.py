"""Mint a scoped ktx_ extension token for the prisma cookie-rotation cron (#10).

Inserts an ExtensionToken row storing ONLY the SHA-256 of the raw token. The
raw token is written to STDOUT with no trailing newline; all diagnostics go to
STDERR. The owner identity is a clearly-labeled non-admin synthetic subject, so
the token's only effective power is cookie rotation + its own owner-scoped jobs
(extension_token auth state — never machine_bearer/trusted_lan).
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select

from karaoke.api.auth import new_extension_token, token_hash
from karaoke.db.models import ExtensionToken
from karaoke.db.session import get_session_factory, init_engine

OWNER_SUBJECT = "karaoke:cookie-rotation-cron"
OWNER_DISPLAY = "Cookie rotation cron (prisma)"
LABEL = "prisma launchd cookie-sync (#10)"


async def main() -> None:
    await init_engine()
    factory = get_session_factory()
    raw = new_extension_token()
    digest = token_hash(raw)
    async with factory() as session:
        before = (
            await session.execute(select(func.count()).select_from(ExtensionToken))
        ).scalar_one()
        # Defensive: disable any prior enabled token with the same label so only
        # the freshly-minted one stays valid (re-runs don't accumulate).
        prior = (
            await session.execute(
                select(ExtensionToken).where(
                    ExtensionToken.label == LABEL,
                    ExtensionToken.disabled.is_(False),
                )
            )
        ).scalars().all()
        for row in prior:
            row.disabled = True
        new_row = ExtensionToken(
            token_hash=digest,
            owner_subject=OWNER_SUBJECT,
            owner_email=None,
            owner_display_name=OWNER_DISPLAY,
            label=LABEL,
            disabled=False,
        )
        session.add(new_row)
        await session.commit()
        await session.refresh(new_row)
        print(
            f"minted ext token id={new_row.id} owner={OWNER_SUBJECT} "
            f"label={LABEL!r} hash8={digest[:8]} disabled_prior={len(prior)} "
            f"tokens_before={before}",
            file=sys.stderr,
        )
    sys.stdout.write(raw)
    sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
