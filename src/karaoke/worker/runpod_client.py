"""Real karaoke worker for **RunPod Serverless**.

Mirrors the safety contract of :mod:`karaoke.worker.vast_client`:

* ``_check_daily_cap`` runs *before* any HTTP call; the rolling 24h cost cap
  is computed from the same ``Job.vast_cost_micros`` column the vast worker
  populates (the field is per-job spend regardless of provider).
* The per-job cost cap is enforced **during** the poll loop; if the
  RunPod-reported ``executionTime`` (or our wall-clock estimate) projects
  cost over the ceiling, we ``POST /cancel/{id}`` and raise
  ``RunpodBudgetError``.
* ``finally`` cancels any in-flight job whose terminal status we never saw,
  so a coordinator crash mid-poll cannot leak a billable RunPod worker.

The RunPod handler contract is JSON in / JSON out (see
``docker/runpod/handler.py``)::

    POST   /v1/endpoints/{endpoint_id}/run
           { "input": { "audio_base64": str, "mode": "both" } }
    GET    /v1/endpoints/{endpoint_id}/status/{id}
           { "status": "IN_QUEUE"|"IN_PROGRESS"|"COMPLETED"|"FAILED"
             |"CANCELLED"|"TIMED_OUT", "output": {...}?, "executionTime"?: ms,
             "delayTime"?: ms }
    POST   /v1/endpoints/{endpoint_id}/cancel/{id}

For ``mode="both"`` the handler returns
``{"vocals_b64", "instrumental_b64", "lyrics_txt", "lyrics_json",
"gpu_model", "elapsed_s"}``.
"""
from __future__ import annotations

import base64
import contextlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from karaoke.worker.vast_client import GpuJobResult

RUNPOD_REST = "https://rest.runpod.io/v1"
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}
USER_AGENT = "karaoke-worker/0.1"


class RunpodError(RuntimeError):
    """Any RunPod lifecycle or GPU-call failure."""


class RunpodBudgetError(RunpodError):
    """A budget cap (per-job or daily) was breached; the job is refused/aborted."""


class RunpodFailedError(RunpodError):
    """RunPod reported the job FAILED / TIMED_OUT / CANCELLED externally."""


class RunpodTimeoutError(RunpodError):
    """The poll loop wall-clock exceeded the per-job ceiling without terminal."""


# ---------------------------------------------------------------------------
# tiny http helper (urllib only — no extra runtime dep). Inject for tests.
# ---------------------------------------------------------------------------
def _http(
    method: str,
    url: str,
    api_key: str,
    body: dict | None = None,
    *,
    timeout: int,
) -> tuple[int, dict]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except Exception:  # pragma: no cover - non-JSON error body
            payload = {}
        return exc.code, payload


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------
class RunpodClient:
    """Submit a karaoke job to a RunPod Serverless endpoint and pull the
    artifacts back.

    Test seam: pass ``http=...`` to inject a callable with the same shape as
    :func:`_http` so unit tests can drive the lifecycle without the network.
    """

    def __init__(
        self,
        settings,
        *,
        prior_24h_cost_micros: int = 0,
        http: Callable[..., tuple[int, dict]] | None = None,
    ) -> None:
        self.settings = settings
        self.prior_24h_cost_micros = prior_24h_cost_micros
        self._http = http or _http

    # -- budget --------------------------------------------------------------
    def _check_daily_cap(self) -> None:
        cap = float(self.settings.runpod_daily_cost_cap or 0)
        if cap <= 0:
            return
        spent = self.prior_24h_cost_micros / 1_000_000
        projected = spent + float(self.settings.runpod_max_job_cost or 0)
        if spent >= cap or projected > cap:
            raise RunpodBudgetError(
                f"daily runpod cost cap reached: spent ${spent:.4f} "
                f"+ max-job ${self.settings.runpod_max_job_cost} > cap ${cap}"
            )

    def _project_cost(self, execution_time_ms: float, wall_seconds: float) -> float:
        """USD cost so far. Prefer RunPod's reported execution time; fall back
        to wall clock (overestimates, which is the safe direction)."""
        seconds = max(execution_time_ms / 1000.0, wall_seconds)
        rate = float(self.settings.runpod_hourly_rate_estimate or 0.68)
        return seconds / 3600.0 * rate

    # -- main entrypoint -----------------------------------------------------
    def run(self, mix_wav: Path, work_dir: Path) -> GpuJobResult:
        api_key = self.settings.runpod_api_key.strip()
        endpoint_id = self.settings.runpod_endpoint_id.strip()
        if not api_key:
            raise RunpodError("KARAOKE_RUNPOD_API_KEY is not set")
        if not endpoint_id:
            raise RunpodError("KARAOKE_RUNPOD_ENDPOINT_ID is not set")

        # Daily cap is checked BEFORE any HTTP call.
        self._check_daily_cap()

        max_job_cost = float(self.settings.runpod_max_job_cost or 0)
        poll_interval = float(self.settings.runpod_poll_interval_s or 2.0)
        request_timeout = int(self.settings.runpod_request_timeout_s or 30)
        # Wall-clock ceiling: never poll forever even if RunPod keeps replying.
        # Use the executionTimeoutMs configured on the endpoint as the upper
        # bound, padded a little for queueing.
        wall_ceiling = float(self.settings.runpod_wall_ceiling_s or 900)

        audio_b64 = base64.b64encode(mix_wav.read_bytes()).decode("ascii")
        run_url = f"{RUNPOD_REST}/endpoints/{endpoint_id}/run"
        cancel_url_tpl = f"{RUNPOD_REST}/endpoints/{endpoint_id}/cancel/{{id}}"
        status_url_tpl = f"{RUNPOD_REST}/endpoints/{endpoint_id}/status/{{id}}"

        job_id: str | None = None
        terminal = False
        started = time.monotonic()
        last_status: str | None = None
        last_execution_ms = 0.0

        try:
            # --- submit ---------------------------------------------------
            code, body = self._http(
                "POST",
                run_url,
                api_key,
                {"input": {"audio_base64": audio_b64, "mode": "both"}},
                timeout=request_timeout,
            )
            if code not in (200, 201) or not body.get("id"):
                raise RunpodError(
                    f"runpod /run failed: HTTP {code} body={body!r}"
                )
            job_id = str(body["id"])

            # --- poll until terminal --------------------------------------
            while True:
                wall = time.monotonic() - started
                if wall >= wall_ceiling:
                    raise RunpodTimeoutError(
                        f"runpod poll wall-clock {wall:.0f}s exceeded ceiling "
                        f"{wall_ceiling:.0f}s (job_id={job_id})"
                    )
                # Per-job cost guard. We check BEFORE each poll so a runaway
                # job is killed before the next billing tick.
                projected = self._project_cost(last_execution_ms, wall)
                if max_job_cost > 0 and projected > max_job_cost:
                    raise RunpodBudgetError(
                        f"runpod per-job cost cap breached: projected "
                        f"${projected:.4f} > cap ${max_job_cost:.4f} "
                        f"(execTime={last_execution_ms/1000:.1f}s, "
                        f"wall={wall:.1f}s, job_id={job_id})"
                    )

                code, st = self._http(
                    "GET",
                    status_url_tpl.format(id=job_id),
                    api_key,
                    None,
                    timeout=request_timeout,
                )
                if code != 200:
                    # Treat transient HTTP errors as retriable; if we keep
                    # missing, the wall_ceiling above will trip.
                    time.sleep(poll_interval)
                    continue

                last_status = str(st.get("status") or "").upper()
                last_execution_ms = float(st.get("executionTime") or 0.0)

                if last_status == "COMPLETED":
                    terminal = True
                    output = st.get("output") or {}
                    return self._materialise(
                        output, work_dir, job_id, last_execution_ms, wall
                    )
                if last_status in TERMINAL_STATES:
                    terminal = True  # already terminal — no cancel needed
                    raise RunpodFailedError(
                        f"runpod job {job_id} ended {last_status}: "
                        f"{(st.get('error') or st.get('output') or '')!r}"
                    )

                time.sleep(poll_interval)
        finally:
            # THE safety property: if we exit with a job in flight that we
            # never saw reach a terminal state, cancel it. RunPod cancel is
            # idempotent and a 4xx on already-terminal is harmless.
            if job_id is not None and not terminal:
                with contextlib.suppress(Exception):
                    self._http(
                        "POST",
                        cancel_url_tpl.format(id=job_id),
                        api_key,
                        None,
                        timeout=request_timeout,
                    )

    # -- helpers -------------------------------------------------------------
    def _materialise(
        self,
        output: dict[str, Any],
        work_dir: Path,
        job_id: str,
        execution_ms: float,
        wall_seconds: float,
    ) -> GpuJobResult:
        work_dir.mkdir(parents=True, exist_ok=True)
        for key in ("vocals_b64", "instrumental_b64", "lyrics_txt", "lyrics_json"):
            if key not in output:
                raise RunpodError(
                    f"runpod COMPLETED but output missing {key!r}; "
                    f"keys={sorted(output)}"
                )

        vocals_path = work_dir / "vocals.wav"
        vocals_path.write_bytes(base64.b64decode(output["vocals_b64"]))
        instrumental_path = work_dir / "instrumental.wav"
        instrumental_path.write_bytes(base64.b64decode(output["instrumental_b64"]))

        lyrics_txt_path = work_dir / "lyrics.txt"
        lyrics_txt_path.write_text(str(output["lyrics_txt"]), encoding="utf-8")
        lyrics_json_path = work_dir / "lyrics.json"
        lj = output["lyrics_json"]
        if not isinstance(lj, str):
            lj = json.dumps(lj, ensure_ascii=False, indent=2)
        lyrics_json_path.write_text(lj, encoding="utf-8")

        # Prefer RunPod's reported execution time; fall back to wall clock.
        seconds = max(execution_ms / 1000.0, wall_seconds)
        rate = float(self.settings.runpod_hourly_rate_estimate or 0.68)
        cost = seconds / 3600.0 * rate

        gpu_model = str(output.get("gpu_model") or "")

        return GpuJobResult(
            vast_instance_id=f"runpod-{job_id}",
            vast_cost=cost,
            gpu_model=gpu_model,
            vocals_path=vocals_path,
            instrumental_path=instrumental_path,
            lyrics_txt_path=lyrics_txt_path,
            lyrics_json_path=lyrics_json_path,
        )
