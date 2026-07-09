"""SPA static serving with cache-aware ``Cache-Control`` headers (issue #122).

Vite emits content-hashed bundles under ``assets/`` — safe to cache forever.
``index.html`` (and any other root file: favicon etc.) must always revalidate,
otherwise a heuristically cached index keeps referencing old hashed bundles
after a release ("cached bundle trap").
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import PathLike, StaticFiles
from starlette.types import Scope

#: Vite content-hashed output under ``assets/`` — new content gets a new URL.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
#: Always revalidate; ETag/Last-Modified conditionals still yield 304s.
NO_CACHE_CACHE_CONTROL = "no-cache"
#: Favicons / manifest change only at a rebrand — cache a day, still revalidate.
ICON_CACHE_CONTROL = "public, max-age=86400"

#: Root-served favicon family (issue #205). The ``/app`` SPA mount already
#: serves these under ``/app/…``, but browsers, bookmark tools, link unfurlers,
#: and iOS request them at the *site root* by convention — and the SSR
#: ``/share/{token}`` page (also rooted) links them root-absolute. Each maps a
#: public path to the ``(filename in the built SPA dist, media type)`` to serve.
#: ``favicon.png`` is byte-identical to the legacy single icon; the rest widen
#: coverage (multi-res .ico, scalable .svg, apple-touch, manifest icons).
ROOT_ICON_ROUTES: dict[str, tuple[str, str]] = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/favicon.png": ("favicon.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    # iOS also probes the -precomposed variant; serve the same asset.
    "/apple-touch-icon-precomposed.png": ("apple-touch-icon.png", "image/png"),
    "/site.webmanifest": ("site.webmanifest", "application/manifest+json"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}


def register_root_icons(app: FastAPI, dist: Path) -> None:
    """Serve the favicon family at the site root from the built SPA ``dist``.

    The files live once in ``web/spa/public/`` (single-sourced from
    ``extension/chrome/icons/mark.svg`` via ``render-icons.mjs``) and Vite
    copies them into ``dist`` at build, so the ``/app`` mount and these root
    routes serve the same bytes. A missing file (SPA never built, or asset
    absent) yields a 404 rather than a 500 — the deployed image always carries
    the full set. Registered before any catch-all so the API's JSON 404 no
    longer answers ``/favicon.ico`` (issue #205).
    """
    for route_path, (filename, media_type) in ROOT_ICON_ROUTES.items():
        file_path = dist / filename

        def _make_handler(path: Path, mtype: str):
            async def _serve_icon() -> FileResponse:
                if not path.is_file():
                    raise HTTPException(status_code=404, detail="not found")
                return FileResponse(
                    path=str(path),
                    media_type=mtype,
                    headers={"Cache-Control": ICON_CACHE_CONTROL},
                )

            return _serve_icon

        app.add_api_route(
            route_path,
            _make_handler(file_path, media_type),
            methods=["GET"],
            include_in_schema=False,
        )


class SpaStaticFiles(StaticFiles):
    """``StaticFiles`` that sets ``Cache-Control`` per file class.

    Starlette's ``html=True`` directory fallback for ``/app/`` also routes
    through :meth:`file_response`, so this single override covers both
    ``/app/`` and ``/app/index.html``. The header is set on whatever response
    comes back — plain ``FileResponse`` or 304 ``NotModifiedResponse`` alike —
    so conditional requests keep it too.
    """

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = self._cache_control_for(full_path)
        return response

    def _cache_control_for(self, full_path: PathLike) -> str:
        try:
            # ``lookup_path`` hands us a realpath; resolve the configured
            # directory the same way before computing the relative location.
            relative = Path(full_path).relative_to(Path(self.directory).resolve())
        except (TypeError, ValueError):
            return NO_CACHE_CACHE_CONTROL
        if relative.parts[:1] == ("assets",):
            return IMMUTABLE_CACHE_CONTROL
        return NO_CACHE_CACHE_CONTROL
