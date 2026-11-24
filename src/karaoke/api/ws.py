"""WebSocket live-progress channel (issue #8).

The worker pushes typed events into an in-process :class:`ProgressHub`; the
API fan-outs them to every subscribed WebSocket client. WS is the canonical
progress channel — ``/jobs/{id}/status`` polling stays supported as fallback.

Endpoints (served by the same uvicorn listener as HTTP, default ``:13140``;
``:13141`` is reserved for a split WS listener — see ``Settings.ws_port``):

* ``WS /ws``          — global broadcast feed of all events. For backward
  compatibility a client may send ``{"action": "subscribe", "job_id": N}``
  to narrow the feed to one job (the pre-#8 protocol); the server then
  replays that job's latest known stage/heartbeat.
* ``WS /ws/{job_id}`` — per-job feed. On connect the server immediately
  replays the latest known ``stage_change`` (from the hub cache, falling
  back to a DB snapshot after a restart) plus the latest ``heartbeat``, so
  a late subscriber sees the current state without waiting for the next
  transition.

Event vocabulary (flat JSON objects; ``ts`` is an ISO-8601 UTC timestamp):

* ``stage_change`` — ``{type, job_id, status, progress, stage_note, error, ts}``
  with ``status`` one of ``queued`` / ``downloading`` / ``separating`` /
  ``transcribing`` / ``finalizing`` / ``completed`` / ``failed`` (plus
  ``cancelled`` for user cancels). ``finalizing`` is a WS-only stage emitted
  between the GPU window and completion; it is not persisted on the Job row.
* ``heartbeat``    — ``{type, job_id, status, progress, ts}``; emitted every
  ``Settings.ws_heartbeat_interval_s`` seconds (default 5s) while a job is in
  a non-terminal stage.
* ``cost_update``  — ``{type, job_id, vast_cost, vast_instance_id, phase, ts}``;
  ``phase`` is ``"provisioned"`` (vast instance created, accrual starts) or
  ``"teardown"`` (GPU window closed, final cost known).
* ``error``        — ``{type, job_id, error, ts}``.

The ordinary job protocol remains compatible with pre-#8 clients. Catalog
owner links require the owning Clerk subject on both replay and live frames.
Browser clients authenticate with the existing first frame on the global
feed. Clients opening a protected per-job feed use the Authorization header.
Pilot processing events are internal.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.exc import SQLAlchemyError

from karaoke.api.auth import _bearer_token, _owner_from_clerk_claims, _validate_clerk_jwt
from karaoke.catalog_identity import CATALOG_OWNER_LINK_PREFIX, PILOT_OWNER
from karaoke.config import get_settings
from karaoke.db.models import Job, JobStatus

_log = logging.getLogger(__name__)

ws_router = APIRouter()

# Event types.
STAGE_CHANGE = "stage_change"
HEARTBEAT = "heartbeat"
COST_UPDATE = "cost_update"
ERROR = "error"

# WS-only synthetic stage between the GPU window and ``completed`` (the DB
# enum has no ``finalizing``; the Job row stays ``transcribing`` meanwhile).
STAGE_FINALIZING = "finalizing"

_TERMINAL_VALUES = frozenset(
    {JobStatus.completed.value, JobStatus.failed.value, JobStatus.cancelled.value}
)

# Per-subscriber queue bound. A slow client loses the OLDEST frames first —
# the newest state always wins (progress events are idempotent snapshots).
_QUEUE_MAX = 256


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _status_value(status: JobStatus | str) -> str:
    return status.value if isinstance(status, JobStatus) else str(status)


# ---------------------------------------------------------------------------
# event constructors
# ---------------------------------------------------------------------------
def make_stage_event(
    job_id: int,
    status: JobStatus | str,
    progress: int,
    *,
    stage_note: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "type": STAGE_CHANGE,
        "job_id": job_id,
        "status": _status_value(status),
        "progress": progress,
        "stage_note": stage_note,
        "error": error,
        "ts": _utcnow_iso(),
    }


def make_heartbeat_event(
    job_id: int, status: str, progress: int | None
) -> dict[str, Any]:
    return {
        "type": HEARTBEAT,
        "job_id": job_id,
        "status": status,
        "progress": progress,
        "ts": _utcnow_iso(),
    }


def make_cost_event(
    job_id: int,
    vast_cost: float,
    *,
    vast_instance_id: str | None = None,
    phase: str = "teardown",
) -> dict[str, Any]:
    return {
        "type": COST_UPDATE,
        "job_id": job_id,
        "vast_cost": round(float(vast_cost), 6),
        "vast_instance_id": vast_instance_id,
        "phase": phase,
        "ts": _utcnow_iso(),
    }


def make_error_event(job_id: int, message: str) -> dict[str, Any]:
    return {
        "type": ERROR,
        "job_id": job_id,
        "error": message,
        "ts": _utcnow_iso(),
    }


# ---------------------------------------------------------------------------
# hub
# ---------------------------------------------------------------------------
class ProgressHub:
    """In-process pub/sub: workers publish, WS connections subscribe.

    Single-process by design — the worker runs inside the API process (see
    ``worker.scheduler``), so an in-process queue is the documented choice
    over Redis pub/sub in the issue. The hub also:

    * caches the latest ``stage_change`` / ``heartbeat`` per job so a late
      subscriber gets an immediate replay on connect;
    * runs one heartbeat task per non-terminal job, publishing a
      ``heartbeat`` every ``Settings.ws_heartbeat_interval_s`` seconds.

    ``publish`` must be called on the event-loop thread (workers are asyncio
    tasks on the app loop); threaded code (e.g. the vast provisioning
    callback inside ``asyncio.to_thread``) uses ``publish_threadsafe``.
    """

    def __init__(self) -> None:
        self._global: set[asyncio.Queue] = set()
        self._per_job: dict[int, set[asyncio.Queue]] = {}
        self._latest_stage: dict[int, dict[str, Any]] = {}
        self._latest_heartbeat: dict[int, dict[str, Any]] = {}
        self._heartbeats: dict[int, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- loop binding ---------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Pin the loop used by ``publish_threadsafe`` (called at app startup)."""
        self._loop = loop

    def _bind_running_loop(self) -> None:
        if self._loop is None or self._loop.is_closed():
            with contextlib.suppress(RuntimeError):
                self._loop = asyncio.get_running_loop()

    # -- subscriptions ---------------------------------------------------------
    def subscribe(self, job_id: int | None = None) -> asyncio.Queue:
        """Register a subscriber queue; ``job_id=None`` joins the global feed."""
        self._bind_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        if job_id is None:
            self._global.add(queue)
        else:
            self._per_job.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue, job_id: int | None = None) -> None:
        if job_id is None:
            self._global.discard(queue)
        else:
            subs = self._per_job.get(job_id)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    self._per_job.pop(job_id, None)

    # -- replay cache -----------------------------------------------------------
    def latest_stage(self, job_id: int) -> dict[str, Any] | None:
        return self._latest_stage.get(job_id)

    def latest_heartbeat(self, job_id: int) -> dict[str, Any] | None:
        return self._latest_heartbeat.get(job_id)

    def forget(self, job_id: int) -> None:
        """Drop a job's caches + heartbeat task (the job row was deleted)."""
        task = self._heartbeats.pop(job_id, None)
        if task is not None:
            task.cancel()
        self._latest_stage.pop(job_id, None)
        self._latest_heartbeat.pop(job_id, None)

    # -- publishing --------------------------------------------------------------
    def publish(self, event: dict[str, Any]) -> None:
        """Cache + fan out one event. Must run on the event-loop thread."""
        self._bind_running_loop()
        etype = event.get("type")
        job_id = event.get("job_id")
        if isinstance(job_id, int):
            if etype == STAGE_CHANGE:
                self._latest_stage[job_id] = event
                self._sync_heartbeat_task(job_id, str(event.get("status") or ""))
            elif etype == HEARTBEAT:
                self._latest_heartbeat[job_id] = event
        self._fanout(event)

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        """Schedule ``publish`` onto the bound loop from a worker thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            _log.debug(
                "ws hub: dropping %s event for job %s (no event loop bound)",
                event.get("type"), event.get("job_id"),
            )
            return
        loop.call_soon_threadsafe(self.publish, event)

    def _fanout(self, event: dict[str, Any]) -> None:
        job_id = event.get("job_id")
        targets = list(self._global)
        if isinstance(job_id, int):
            targets.extend(self._per_job.get(job_id, ()))
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Evict the oldest frame so the newest state wins.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    # -- heartbeats ----------------------------------------------------------------
    def _sync_heartbeat_task(self, job_id: int, status: str) -> None:
        if status in _TERMINAL_VALUES:
            task = self._heartbeats.pop(job_id, None)
            if task is not None:
                task.cancel()
            # A terminal job has no "in progress" heartbeat to replay.
            self._latest_heartbeat.pop(job_id, None)
            return
        if job_id in self._heartbeats:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - publish outside a loop
            return
        self._heartbeats[job_id] = loop.create_task(self._heartbeat_loop(job_id))

    async def _heartbeat_loop(self, job_id: int) -> None:
        interval = max(0.01, float(get_settings().ws_heartbeat_interval_s or 5.0))
        while True:
            await asyncio.sleep(interval)
            stage = self._latest_stage.get(job_id)
            if stage is None:  # pragma: no cover - cache dropped mid-flight
                continue
            self.publish(
                make_heartbeat_event(
                    job_id,
                    status=str(stage.get("status") or ""),
                    progress=stage.get("progress"),
                )
            )

    async def aclose(self) -> None:
        """Cancel heartbeat tasks (app shutdown)."""
        tasks = list(self._heartbeats.values())
        self._heartbeats.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


# ---------------------------------------------------------------------------
# hub singleton
# ---------------------------------------------------------------------------
_hub: ProgressHub | None = None


def get_hub() -> ProgressHub:
    """Process-wide hub (the worker runs in the API process)."""
    global _hub
    if _hub is None:
        _hub = ProgressHub()
    return _hub


async def shutdown_hub() -> None:
    """Tear down the hub at app shutdown and drop the singleton."""
    global _hub
    hub, _hub = _hub, None
    if hub is not None:
        await hub.aclose()


def reset_hub_for_tests() -> None:
    """Drop the singleton (tests simulate a restart / isolate state)."""
    global _hub
    if _hub is not None:
        for task in _hub._heartbeats.values():
            task.cancel()
    _hub = None


# ---------------------------------------------------------------------------
# worker-facing publish helpers (never raise into the pipeline)
# ---------------------------------------------------------------------------
def publish_stage(
    job_id: int,
    status: JobStatus | str,
    progress: int,
    *,
    stage_note: str | None = None,
    error: str | None = None,
) -> None:
    """Publish a ``stage_change`` from the event-loop thread."""
    with contextlib.suppress(Exception):
        get_hub().publish(
            make_stage_event(
                job_id, status, progress, stage_note=stage_note, error=error
            )
        )


def publish_cost(
    job_id: int,
    vast_cost: float,
    *,
    vast_instance_id: str | None = None,
    phase: str = "teardown",
) -> None:
    """Publish a ``cost_update`` from the event-loop thread."""
    with contextlib.suppress(Exception):
        get_hub().publish(
            make_cost_event(
                job_id, vast_cost, vast_instance_id=vast_instance_id, phase=phase
            )
        )


def publish_cost_threadsafe(
    job_id: int,
    vast_cost: float,
    *,
    vast_instance_id: str | None = None,
    phase: str = "provisioned",
) -> None:
    """Publish a ``cost_update`` from a worker thread (vast provisioning)."""
    with contextlib.suppress(Exception):
        get_hub().publish_threadsafe(
            make_cost_event(
                job_id, vast_cost, vast_instance_id=vast_instance_id, phase=phase
            )
        )


def publish_error(job_id: int, message: str) -> None:
    """Publish an ``error`` event from the event-loop thread."""
    with contextlib.suppress(Exception):
        get_hub().publish(make_error_event(job_id, message))


def forget_job(job_id: int) -> None:
    """Drop a deleted job's cached state so replays can't resurrect it."""
    with contextlib.suppress(Exception):
        get_hub().forget(job_id)


async def _clerk_subject(token: str | None) -> str | None:
    # A service bearer and an extension token do not authenticate catalog
    # access. Bound the input before passing it to the Clerk JWT decoder.
    if not token or len(token) > 8192 or not token.isascii() or token.count(".") != 2:
        return None
    settings = get_settings()
    if secrets.compare_digest(token.encode(), settings.service_token.strip().encode()):
        return None
    # This signature also works in the previous release. This identity is
    # used solely for Clerk catalog scopes, which have no email allowlist.
    catalog_settings = settings.model_copy(update={"auth_allowed_emails": ""})
    try:
        claims = await asyncio.to_thread(_validate_clerk_jwt, token, catalog_settings)
    except HTTPException:
        return None
    owner = _owner_from_clerk_claims(claims)
    return owner.subject if owner is not None else None


async def _load_job(job_id: int) -> Job | None:
    from karaoke.db.session import get_session_factory

    if not 0 < job_id <= 2_147_483_647:
        return None
    try:
        factory = get_session_factory()
        async with factory() as session:
            return await session.get(Job, job_id)
    except (RuntimeError, SQLAlchemyError):
        # Without a row (or a working DB), a cached event cannot prove its
        # scope. In particular, never replay queued frames from a deleted link.
        return None


class _CatalogFeedAccess:
    def __init__(self, subject: str | None) -> None:
        self.subject = subject
        # A job's owner and reserved identity are assigned before its first
        # publication and never change. Cache scopes, not auth decisions.
        self._scopes: dict[int, tuple[bool, str | None]] = {}

    async def allows(self, job_id: int) -> bool:
        scope = self._scopes.get(job_id)
        if scope is None:
            job = await _load_job(job_id)
            if job is None:
                return False
            owner = (
                job.owner_subject
                if job.job_token.startswith(CATALOG_OWNER_LINK_PREFIX)
                or getattr(job, "catalog_asset_id", None) is not None
                else None
            )
            scope = (job.owner_subject == PILOT_OWNER, owner)
            if len(self._scopes) >= _QUEUE_MAX:
                self._scopes.clear()
            self._scopes[job_id] = scope
        internal, owner = scope
        return not internal and (owner is None or owner == self.subject)


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------
async def _db_stage_snapshot(job_id: int) -> dict[str, Any] | None:
    """Synthesize a ``stage_change`` from the Job row (hub cache miss —
    e.g. the first subscriber after a process restart)."""
    job = await _load_job(job_id)
    if job is None:
        return None
    return make_stage_event(
        job.id, job.status, job.progress, stage_note=job.stage_note, error=job.error
    )


async def _send_replay(
    websocket: WebSocket,
    hub: ProgressHub,
    job_id: int,
    send_lock: asyncio.Lock,
    access: _CatalogFeedAccess,
) -> bool:
    """Replay the latest stage + heartbeat for ``job_id`` on connect.

    Returns False (after sending an ``error`` event) when the job does not
    exist or is hidden from this connection.
    """
    stage = None
    if await access.allows(job_id):
        stage = hub.latest_stage(job_id)
        if stage is None:
            stage = await _db_stage_snapshot(job_id)
    if stage is None:
        async with send_lock:
            await websocket.send_json(make_error_event(job_id, "job not found"))
        return False
    async with send_lock:
        await websocket.send_json(stage)
    heartbeat = hub.latest_heartbeat(job_id)
    if heartbeat is not None:
        async with send_lock:
            await websocket.send_json(heartbeat)
    return True


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
async def _run_until_disconnect(*coros) -> None:
    """Run sender/receiver coroutines until the first one ends (disconnect)."""
    tasks = [asyncio.ensure_future(coro) for coro in coros]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@ws_router.websocket("/ws")
async def websocket_feed(websocket: WebSocket) -> None:
    """Global broadcast feed of ordinary and owner-visible job events.

    Back-compat: a client may send ``{"action": "subscribe", "job_id": N}``
    (the pre-#8 protocol) at any time to narrow the feed to one job; the
    server replies with that job's replay frames first.
    """
    access = _CatalogFeedAccess(await _clerk_subject(_bearer_token(websocket)))
    await websocket.accept()
    hub = get_hub()
    queue = hub.subscribe(None)
    send_lock = asyncio.Lock()
    state: dict[str, int | None] = {"filter": None}

    async def _recv() -> None:
        while True:
            try:
                msg = await websocket.receive_json()
            except ValueError:
                continue  # ignore non-JSON frames (e.g. wscat keystrokes)
            if not isinstance(msg, dict):
                continue
            if msg.get("action") == "auth":
                token = msg.get("token")
                async with send_lock:
                    access.subject = None
                subject = await _clerk_subject(token if isinstance(token, str) else None)
                async with send_lock:
                    access.subject = subject
                continue
            job_id = msg.get("job_id")
            if type(job_id) is int:
                state["filter"] = job_id
                await _send_replay(websocket, hub, job_id, send_lock, access)

    async def _pump() -> None:
        while True:
            event = await queue.get()
            job_id = event.get("job_id")
            if not isinstance(job_id, int):
                continue
            async with send_lock:
                target = state["filter"]
                if (target is None or target == job_id) and await access.allows(job_id):
                    await websocket.send_json(event)

    try:
        await _run_until_disconnect(_recv(), _pump())
    except WebSocketDisconnect:  # pragma: no cover - client vanished mid-send
        pass
    finally:
        hub.unsubscribe(queue, None)
        with contextlib.suppress(Exception):
            await websocket.close()


@ws_router.websocket("/ws/{job_id}")
async def websocket_job_feed(websocket: WebSocket, job_id: int) -> None:
    """Per-job feed: replay the latest stage/heartbeat, then stream live."""
    access = _CatalogFeedAccess(await _clerk_subject(_bearer_token(websocket)))
    await websocket.accept()
    hub = get_hub()
    queue = hub.subscribe(job_id)
    send_lock = asyncio.Lock()
    try:
        if not await _send_replay(websocket, hub, job_id, send_lock, access):
            return

        async def _recv() -> None:
            # Drain incoming frames so close frames are processed promptly.
            while True:
                with contextlib.suppress(ValueError):
                    await websocket.receive_json()

        async def _pump() -> None:
            while True:
                event = await queue.get()
                async with send_lock:
                    await websocket.send_json(event)

        await _run_until_disconnect(_recv(), _pump())
    except WebSocketDisconnect:  # pragma: no cover - client vanished mid-send
        pass
    finally:
        hub.unsubscribe(queue, job_id)
        with contextlib.suppress(Exception):
            await websocket.close()
