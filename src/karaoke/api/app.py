"""FastAPI application factory for karaoke."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from karaoke.api.routes import router
from karaoke.config import Settings, get_settings
from karaoke.db.session import init_engine, shutdown_engine
from karaoke.web.views import router as web_router


def _cors_origins(settings: Settings) -> list[str]:
    raw = settings.cors_origins
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _warm_jwks(settings: Settings) -> None:
    """Pre-fetch the Clerk JWKS so the first authed request is fast.

    Failures here are non-fatal — the live request path will report 503
    with a sensible message if Clerk is misconfigured.
    """
    if not settings.clerk_jwks_url.strip() and not settings.clerk_jwks_json.strip():
        return
    try:
        from karaoke.api.auth import _load_jwks  # local import to avoid cycle

        _load_jwks(settings)
    except Exception:  # pragma: no cover - non-fatal warm-up
        return


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await init_engine(settings.database_url)
    _warm_jwks(settings)
    try:
        yield
    finally:
        await shutdown_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` instance."""
    settings = settings or get_settings()
    app = FastAPI(
        title="karaoke",
        version="0.1.0",
        description="URL → vocals + instrumental playback + lyrics. Coordinator API.",
        lifespan=lifespan,
    )

    origins = _cors_origins(settings)
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(router)
    app.include_router(web_router)
    return app
