"""WebSocket progress hub for karaoke jobs.

This is intentionally in-process for the current single-coordinator runtime.
The public functions are the worker-facing seam; swapping them for Redis
pub/sub later should not require changing the worker pipeline.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from karaoke.config import get_settings
from karaoke.db.models import Job, JobStatus
from karaoke.db.session import get_session_factory

router = APIRouter()

STAGE_CHANGE = "stage_change"
HEARTBEAT = "heartbeat"
COST_UPDATE = "cost_update"
ERROR = "error"

_TERMINAL_STAGES = {
    JobStatus.completed.value,
    JobStatus.failed.value,
    JobStatus.cancelled.value,
}

_main_loop: asyncio.AbstractEventLoop | None = None


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def bind_event_loop(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Record the API event loop for worker callbacks running in threads."""
    global _main_loop
    _main_loop = loop or asyncio.get_running_loop()


def stage_event(
    job_id: int,
    stage: str | JobStatus,
    progress: int,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    value = stage.value if isinstance(stage, JobStatus) else stage
    return {
        "type": STAGE_CHANGE,
        "job_id": job_id,
        "stage": value,
        # Compatibility with the original SPA/test shape.
        "status": value,
        "progress": progress,
        "error": error,
        "ts": _now_iso(),
    }


def cost_event(
    job_id: int,
    vast_cost: float,
    *,
    vast_instance_id: str | int | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": COST_UPDATE,
        "job_id": job_id,
        "vast_cost": round(float(vast_cost), 6),
        "ts": _now_iso(),
    }
    if vast_instance_id is not None:
        event["vast_instance_id"] = str(vast_instance_id)
    return event


def error_event(job_id: int, message: str) -> dict[str, Any]:
    return {
        "type": ERROR,
        "job_id": job_id,
        "error": message,
        "ts": _now_iso(),
    }


class ProgressHub:
    """Connection registry and fan-out for job progress events."""

    def __init__(self) -> None:
        self._global: set[WebSocket] = set()
        self._by_job: dict[int, set[WebSocket]] = defaultdict(set)
        self._latest_stage: dict[int, dict[str, Any]] = {}
        self._latest_heartbeat: dict[int, dict[str, Any]] = {}
        self._heartbeat_tasks: dict[int, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, job_id: int | None) -> None:
        await websocket.accept()
        async with self._lock:
            if job_id is None:
                self._global.add(websocket)
                replay = list(self._latest_stage.values())
            else:
                self._by_job[job_id].add(websocket)
                replay = self.replay_events(job_id)
        for event in replay:
            await self._safe_send(websocket, event)

    async def disconnect(self, websocket: WebSocket, job_id: int | None) -> None:
        async with self._lock:
            if job_id is None:
                self._global.discard(websocket)
            else:
                sockets = self._by_job.get(job_id)
                if sockets is not None:
                    sockets.discard(websocket)
                    if not sockets:
                        self._by_job.pop(job_id, None)

    def has_stage(self, job_id: int) -> bool:
        return job_id in self._latest_stage

    def replay_events(self, job_id: int) -> list[dict[str, Any]]:
        replay: list[dict[str, Any]] = []
        if stage := self._latest_stage.get(job_id):
            replay.append(stage)
        if heartbeat := self._latest_heartbeat.get(job_id):
            replay.append(heartbeat)
        return replay

    async def publish(self, event: dict[str, Any]) -> None:
        job_id_raw = event.get("job_id")
        job_id = int(job_id_raw) if isinstance(job_id_raw, int | str) else None
        if job_id is not None:
            if event.get("type") == STAGE_CHANGE:
                self._latest_stage[job_id] = event
                self._sync_heartbeat(job_id, str(event.get("stage") or ""))
            elif event.get("type") == HEARTBEAT:
                self._latest_heartbeat[job_id] = event

        async with self._lock:
            sockets = set(self._global)
            if job_id is not None:
                sockets.update(self._by_job.get(job_id, set()))

        stale: list[WebSocket] = []
        for websocket in sockets:
            if not await self._safe_send(websocket, event):
                stale.append(websocket)
        for websocket in stale:
            await self.disconnect(websocket, None)
            if job_id is not None:
                await self.disconnect(websocket, job_id)

    async def _safe_send(self, websocket: WebSocket, event: dict[str, Any]) -> bool:
        try:
            await websocket.send_json(event)
            return True
        except Exception:
            return False

    def _sync_heartbeat(self, job_id: int, stage: str) -> None:
        if stage in _TERMINAL_STAGES:
            task = self._heartbeat_tasks.pop(job_id, None)
            if task is not None:
                task.cancel()
            return
        if job_id not in self._heartbeat_tasks:
            self._heartbeat_tasks[job_id] = asyncio.create_task(
                self._heartbeat_loop(job_id)
            )

    async def _heartbeat_loop(self, job_id: int) -> None:
        try:
            while True:
                interval = max(0.1, float(get_settings().ws_heartbeat_interval_s))
                await asyncio.sleep(interval)
                stage = self._latest_stage.get(job_id)
                if stage is None:
                    continue
                if str(stage.get("stage") or "") in _TERMINAL_STAGES:
                    return
                await self.publish(
                    {
                        "type": HEARTBEAT,
                        "job_id": job_id,
                        "stage": stage.get("stage"),
                        "status": stage.get("status"),
                        "progress": stage.get("progress"),
                        "interval_s": interval,
                        "ts": _now_iso(),
                    }
                )
        except asyncio.CancelledError:
            return
        finally:
            self._heartbeat_tasks.pop(job_id, None)

    async def close(self) -> None:
        tasks = list(self._heartbeat_tasks.values())
        self._heartbeat_tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._global.clear()
        self._by_job.clear()
        self._latest_stage.clear()
        self._latest_heartbeat.clear()

    def reset(self) -> None:
        for task in self._heartbeat_tasks.values():
            task.cancel()
        self._heartbeat_tasks.clear()
        self._global.clear()
        self._by_job.clear()
        self._latest_stage.clear()
        self._latest_heartbeat.clear()


hub = ProgressHub()


async def publish_event(event: dict[str, Any]) -> None:
    await hub.publish(event)


def publish_event_threadsafe(event: dict[str, Any]) -> None:
    """Publish from sync code, including callbacks running inside to_thread."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = _main_loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(hub.publish(event), loop)
    else:
        loop.create_task(hub.publish(event))


async def publish_stage(
    job_id: int,
    stage: str | JobStatus,
    progress: int,
    *,
    error: str | None = None,
) -> None:
    await publish_event(stage_event(job_id, stage, progress, error=error))


async def publish_cost_update(
    job_id: int,
    vast_cost: float,
    *,
    vast_instance_id: str | int | None = None,
) -> None:
    await publish_event(
        cost_event(job_id, vast_cost, vast_instance_id=vast_instance_id)
    )


async def publish_error(job_id: int, message: str) -> None:
    await publish_event(error_event(job_id, message))


def publish_cost_update_threadsafe(
    job_id: int,
    vast_cost: float,
    *,
    vast_instance_id: str | int | None = None,
) -> None:
    publish_event_threadsafe(
        cost_event(job_id, vast_cost, vast_instance_id=vast_instance_id)
    )


async def _send_db_snapshot(websocket: WebSocket, job_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(Job, job_id)
    if job is None:
        await websocket.send_json(error_event(job_id, "job not found"))
        return
    await websocket.send_json(
        stage_event(job.id, job.status.value, job.progress, error=job.error)
    )
    if job.vast_cost_micros is not None:
        await websocket.send_json(
            cost_event(
                job.id,
                job.vast_cost_micros / 1_000_000,
                vast_instance_id=job.vast_instance_id,
            )
        )


async def _socket_loop(websocket: WebSocket, job_id: int | None) -> None:
    await hub.connect(websocket, job_id)
    if job_id is not None and not hub.has_stage(job_id):
        await _send_db_snapshot(websocket, job_id)
    try:
        while True:
            # The broadcast endpoint ignores inbound messages. This keeps the
            # legacy {"action":"subscribe","job_id":N} client harmless while
            # /ws now behaves as the global feed specified by the PRD.
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
    finally:
        await hub.disconnect(websocket, job_id)


@router.websocket("/ws")
async def websocket_broadcast(websocket: WebSocket) -> None:
    await _socket_loop(websocket, None)


@router.websocket("/ws/{job_id}")
async def websocket_job(websocket: WebSocket, job_id: int) -> None:
    await _socket_loop(websocket, job_id)
