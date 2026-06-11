"""In-process, ephemeral per-job YouTube cookie carrier (issue #77).

A client (Chrome extension on desktop, native app on mobile) may submit the
user's logged-in YouTube cookies *with* a job (``POST /jobs``). Those cookies
are needed only for that one yt-dlp download of a session-gated video.

This module is the hand-off between the request handler and the in-process
worker task. The contract:

- Cookies live **only in memory**, keyed by job id, and **only** between job
  creation and the worker's download stage. They are NEVER written to the DB
  and NEVER logged.
- The worker pops them, writes them to a per-job ``0600`` temp for the single
  yt-dlp invocation, and deletes that temp after the download stage and on
  failure (see ``pipeline._ytdlp_aux_args``).
- A process restart drops this registry, so no cookie blob ever survives a
  restart — the ephemeral guarantee holds end to end. Boot reconcile (#130)
  then fails out the in-flight job (cookies already consumed or moot), or
  re-dispatches a still-``queued`` one WITHOUT its original cookies: a gated
  video then fails with the normal gated-video error (there is no central
  fallback — #132), and the user resubmits via the extension. Accepted by
  the #77 decision record.

The API handler and the worker task run on the same asyncio event loop in the
same process, so a plain dict needs no locking.
"""
from __future__ import annotations

# job_id -> raw Netscape cookie blob. Holds at most one entry per in-flight job
# carrying cookies, for the brief window between submission and the worker's
# download stage. Never serialised, never logged.
_PENDING: dict[int, str] = {}


def stash(job_id: int, blob: str) -> None:
    """Hold ``blob`` for ``job_id`` until the worker consumes it."""
    _PENDING[job_id] = blob


def pop(job_id: int) -> str | None:
    """Return and remove the cookies stashed for ``job_id`` (``None`` if none)."""
    return _PENDING.pop(job_id, None)


def discard(job_id: int) -> None:
    """Drop any cookies stashed for ``job_id`` without using them."""
    _PENDING.pop(job_id, None)
