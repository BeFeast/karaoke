"""SPA static serving with cache-aware ``Cache-Control`` headers (issue #122).

Vite emits content-hashed bundles under ``assets/`` — safe to cache forever.
``index.html`` (and any other root file: favicon etc.) must always revalidate,
otherwise a heuristically cached index keeps referencing old hashed bundles
after a release ("cached bundle trap").
"""
from __future__ import annotations

import os
from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import PathLike, StaticFiles
from starlette.types import Scope

#: Vite content-hashed output under ``assets/`` — new content gets a new URL.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
#: Always revalidate; ETag/Last-Modified conditionals still yield 304s.
NO_CACHE_CACHE_CONTROL = "no-cache"


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
