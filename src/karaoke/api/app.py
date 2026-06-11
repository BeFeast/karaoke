"""FastAPI application factory for karaoke."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from karaoke import __version__
from karaoke.api.routes import router
from karaoke.api.spa_static import SpaStaticFiles
from karaoke.api.ws import get_hub, shutdown_hub, ws_router
from karaoke.config import Settings, get_settings
from karaoke.db.session import init_engine, shutdown_engine


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
    # Pin the progress hub to the app loop so worker threads (e.g. the vast
    # provisioning callback) can publish WS events thread-safely (issue #8).
    get_hub().bind_loop(asyncio.get_running_loop())
    _warm_jwks(settings)
    try:
        yield
    finally:
        await shutdown_hub()
        await shutdown_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` instance."""
    settings = settings or get_settings()
    app = FastAPI(
        title="karaoke",
        # Deploy-truth version (driven by pyproject.toml via importlib.metadata)
        # so the OpenAPI doc and /health both report the released tag.
        version=__version__,
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
    app.include_router(ws_router)

    # Root redirect → Submitter SPA. Exact-path only, so it never shadows
    # /health, /jobs, /share, /me, /config, or /ws.
    @app.get("/", include_in_schema=False)
    async def _spa_root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/app/")

    # Serve the built Submitter SPA at /app, but only when a build exists —
    # dev/test runs without a `vite build` still boot cleanly. SpaStaticFiles
    # sets Cache-Control: no-cache on index.html (and other root files) and
    # immutable on hashed assets/, so a release never leaves browsers with a
    # stale index referencing old bundles (issue #122).
    spa = Path(settings.spa_dist_path)
    if spa.is_dir():
        app.mount("/app", SpaStaticFiles(directory=str(spa), html=True), name="spa")

    return app
