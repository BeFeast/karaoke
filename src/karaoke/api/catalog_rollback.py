"""Keep catalog rows outside the previous release's public and admin APIs."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from sys import argv

from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, with_loader_criteria

from karaoke.catalog_identity import CATALOG_OWNER_LINK_PREFIX, PILOT_OWNER
from karaoke.db.models import Job
from karaoke.db.session import get_session, get_session_factory

# The previous image has no ORM mapping for the nullable catalog foreign key.
# Match the durable reserved owner and token prefixes used by this release.
_SESSION_FLAG = "catalog_rollback_api"


@event.listens_for(AsyncSession.sync_session_class, "do_orm_execute")
def _hide_catalog_jobs(state: ORMExecuteState) -> None:
    if state.is_select and state.session.info.get(_SESSION_FLAG):
        state.statement = state.statement.options(
            with_loader_criteria(
                Job,
                lambda row: (row.owner_subject != PILOT_OWNER)
                & ~row.job_token.startswith(CATALOG_OWNER_LINK_PREFIX),
                include_aliases=True,
            )
        )


async def _legacy_api_session() -> AsyncIterator[AsyncSession]:
    # Only dependency-injected HTTP sessions are filtered. The old worker's
    # rolling GPU budget queries still account for the underlying pilot jobs.
    factory = get_session_factory()
    async with factory() as session:
        session.info[_SESSION_FLAG] = True
        yield session


def protect_legacy_api(app: FastAPI) -> FastAPI:
    app.dependency_overrides[get_session] = _legacy_api_session
    return app


def patch_legacy_factory(path: Path) -> None:
    """Install the guard at build time, keeping the original app entrypoint."""
    old = path.read_text(encoding="utf-8")
    anchor = "    return app\n"
    if old.count(anchor) != 1:
        raise ValueError("unexpected app factory in catalog rollback image")
    path.write_text(
        old.replace(
            anchor,
            "    from karaoke.api.catalog_rollback import protect_legacy_api\n"
            "\n    return protect_legacy_api(app)\n",
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    patch_legacy_factory(Path(argv[1]))
