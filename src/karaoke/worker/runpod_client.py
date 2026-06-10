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

    POST   https://api.runpod.ai/v2/{endpoint_id}/run
           { "input": { "audio_base64": str, "mode": "both" } }
    GET    https://api.runpod.ai/v2/{endpoint_id}/status/{id}
           { "status": "IN_QUEUE"|"IN_PROGRESS"|"COMPLETED"|"FAILED"
             |"CANCELLED"|"TIMED_OUT", "output": {...}?, "executionTime"?: ms,
             "delayTime"?: ms }
    POST   https://api.runpod.ai/v2/{endpoint_id}/cancel/{id}

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

RUNPOD_REST = "https://api.runpod.ai/v2"
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


class RunpodCapacityError(RunpodTimeoutError):
    """The job never left IN_QUEUE before the queue ceiling — a transient GPU
    capacity outage. The queued job is cancelled (no compute ran, no cost), so
    this is safe to retry: the coordinator re-submits with backoff. Subclasses
    RunpodTimeoutError so existing ``except RunpodTimeoutError`` callers and the
    queue-ceiling tests keep working."""


class RunpodColdStartError(RunpodCapacityError):
    """RunPod reports workers initializing, usually while pulling the image."""

    def __init__(self, message: str, *, workers_initializing: int) -> None:
        super().__init__(message)
        self.workers_initializing = workers_initializing


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


def _extract_workers_initializing(body: dict) -> int:
    """Return RunPod endpoint health ``workers.initializing`` defensively."""
    workers = body.get("workers")
    if isinstance(workers, dict):
        value = workers.get("initializing")
        if isinstance(value, (int, float)):
            return max(0, int(value))
        return 0
    if isinstance(workers, list):
        total = 0
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            state = str(
                worker.get("status")
                or worker.get("state")
                or worker.get("desiredStatus")
                or ""
            ).lower()
            if state == "initializing":
                total += 1
        return total
    return 0


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
        r2_uploader: Callable[..., str] | None = None,
    ) -> None:
        self.settings = settings
        self.prior_24h_cost_micros = prior_24h_cost_micros
        self._http = http or _http
        self._r2_uploader = r2_uploader
        self._r2_put_urls: dict[str, str] = {}
        self._r2_output_keys: dict[str, str] = {}

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
    def run(
        self,
        mix_wav: Path,
        work_dir: Path,
        *,
        align_text: str | None = None,
        align_lang: str | None = None,
    ) -> GpuJobResult:
        """Submit one job and pull back the artifacts.

        ``align_text`` (#55): when set, the handler force-aligns this plain
        text against the separated vocal stem and returns a synced LRC in
        ``aligned_lrc``. ``align_lang`` is an ISO-639-3 code. Both are passed
        through verbatim; the handler ignores them if it predates the feature,
        so this is safe against an old image (we just get no ``aligned_lrc``).
        """
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
        # Two-tier timeout (see config). queue_ceiling fails fast while the
        # job waits for a GPU; wall_ceiling is an absolute backstop. Once the
        # job is IN_PROGRESS we never abort it on the queue timer.
        queue_ceiling = float(self.settings.runpod_queue_ceiling_s or 480)
        wall_ceiling = float(self.settings.runpod_wall_ceiling_s or 1200)

        # If R2 is configured, upload mix.wav and pass a presigned URL
        # (RunPod async /run has a ~10MB body cap; our 35-50MB wavs blow it up).
        # Otherwise fall back to base64 (works for tiny test inputs and tests).
        run_input: dict[str, Any]
        if self._r2_configured():
            audio_url, _ = self._upload_to_r2(mix_wav)
            run_input = {"audio_url": audio_url, "mode": "both"}
            if getattr(self, "_r2_put_urls", None):
                run_input["vocals_put_url"] = self._r2_put_urls["vocals"]
                run_input["instrumental_put_url"] = self._r2_put_urls["instrumental"]
        else:
            audio_b64 = base64.b64encode(mix_wav.read_bytes()).decode("ascii")
            run_input = {"audio_base64": audio_b64, "mode": "both"}

        # Force-alignment (#55): pass the plain lyrics to the handler so it can
        # synthesize a synced LRC from the vocal stem inside the same GPU window.
        if align_text and align_text.strip():
            run_input["align_text"] = align_text
            if align_lang and align_lang.strip():
                run_input["align_lang"] = align_lang

        run_url = f"{RUNPOD_REST}/{endpoint_id}/run"
        cancel_url_tpl = f"{RUNPOD_REST}/{endpoint_id}/cancel/{{id}}"
        status_url_tpl = f"{RUNPOD_REST}/{endpoint_id}/status/{{id}}"
        health_url = f"{RUNPOD_REST}/{endpoint_id}/health"

        job_id: str | None = None
        terminal = False
        started = time.monotonic()
        last_status: str | None = None
        last_execution_ms = 0.0
        exec_phase = False  # flips True once RunPod reports IN_PROGRESS

        try:
            # --- submit ---------------------------------------------------
            code, body = self._http(
                "POST",
                run_url,
                api_key,
                {"input": run_input},
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
                # Absolute backstop — only trips if status never advances.
                if wall >= wall_ceiling:
                    raise RunpodTimeoutError(
                        f"runpod poll wall-clock {wall:.0f}s exceeded backstop "
                        f"{wall_ceiling:.0f}s (job_id={job_id}, last={last_status})"
                    )
                # Queue fail-fast: a job that never starts running within the
                # queue ceiling is a GPU capacity outage. NEVER applied once the
                # job is IN_PROGRESS (we don't kill paid, running compute).
                if not exec_phase and wall >= queue_ceiling:
                    initializing = self._workers_initializing(
                        health_url, api_key, request_timeout
                    )
                    if initializing > 0:
                        raise RunpodColdStartError(
                            f"runpod job stuck in queue {wall:.0f}s > "
                            f"{queue_ceiling:.0f}s while "
                            f"{initializing} worker(s) initialize — GPU image "
                            f"pull in progress, retry without burning capacity "
                            f"budget (job_id={job_id})",
                            workers_initializing=initializing,
                        )
                    raise RunpodCapacityError(
                        f"runpod job stuck in queue {wall:.0f}s > "
                        f"{queue_ceiling:.0f}s — GPU capacity busy, retry shortly "
                        f"(job_id={job_id})"
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
                if last_status == "IN_PROGRESS" or last_execution_ms > 0:
                    exec_phase = True  # past the queue — let it run to completion

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

    def _workers_initializing(
        self, health_url: str, api_key: str, request_timeout: int
    ) -> int:
        code, body = self._http("GET", health_url, api_key, None, timeout=request_timeout)
        if code != 200:
            return 0
        return _extract_workers_initializing(body)

    # -- R2 upload (presigned audio URL bypasses RunPod's ~10MB body cap) ----
    def _r2_configured(self) -> bool:
        return bool(
            self.settings.r2_endpoint_url.strip()
            and self.settings.r2_bucket.strip()
            and self.settings.r2_access_key_id.strip()
            and self.settings.r2_secret_access_key.strip()
        )

    def _upload_to_r2(self, mix_wav: Path) -> tuple[str, dict[str, str]]:
        """Upload ``mix_wav`` to R2 under a per-job prefix and return
        ``(audio_get_url, output_keys)`` where ``output_keys`` are the
        per-stem R2 keys we'll pull after COMPLETED. Also stashes
        per-stem presigned PUT URLs on ``self`` so ``run()`` can pass
        them to the handler — the handler streams stems straight to R2
        because RunPod /run output has a ~10MB cap and raw stems blow
        it. Test seam: pass ``r2_uploader`` to __init__."""
        if self._r2_uploader is not None:
            audio_url = self._r2_uploader(mix_wav)
            return audio_url, {"vocals": "", "instrumental": ""}
        from karaoke.worker.r2_client import (
            presign_get,
            presign_put,
            upload_file,
        )

        prefix = f"jobs/{int(time.time())}-{mix_wav.stem}"
        input_key = f"{prefix}/mix.wav"
        output_keys = {
            "vocals": f"{prefix}/vocals.wav",
            "instrumental": f"{prefix}/instrumental.wav",
        }
        upload_file(
            mix_wav,
            endpoint_url=self.settings.r2_endpoint_url,
            bucket=self.settings.r2_bucket,
            key=input_key,
            access_key_id=self.settings.r2_access_key_id,
            secret_access_key=self.settings.r2_secret_access_key,
            content_type="audio/wav",
        )
        audio_url = presign_get(
            endpoint_url=self.settings.r2_endpoint_url,
            bucket=self.settings.r2_bucket,
            key=input_key,
            access_key_id=self.settings.r2_access_key_id,
            secret_access_key=self.settings.r2_secret_access_key,
            expires_in=int(self.settings.r2_presign_ttl_s or 600),
        )
        self._r2_put_urls = {
            kind: presign_put(
                endpoint_url=self.settings.r2_endpoint_url,
                bucket=self.settings.r2_bucket,
                key=k,
                access_key_id=self.settings.r2_access_key_id,
                secret_access_key=self.settings.r2_secret_access_key,
                expires_in=int(self.settings.r2_presign_ttl_s or 600),
            )
            for kind, k in output_keys.items()
        }
        self._r2_output_keys = output_keys
        return audio_url, output_keys

    # -- R2 download (output stems too large for RunPod's 10MB cap) ----------
    def _handle_r2_output(
        self, output: dict[str, Any], work_dir: Path
    ) -> tuple[Path, Path]:
        """Materialise vocals.wav + instrumental.wav into ``work_dir``.

        Three cases:
          1. R2 PUT path (handler set ``vocals_uploaded: True``): GET
             the stems from the keys we generated up front.
          2. base64 path (no R2): decode ``vocals_b64`` /
             ``instrumental_b64`` from the JSON output.
          3. Otherwise: raise.
        """
        vocals_path = work_dir / "vocals.wav"
        instrumental_path = work_dir / "instrumental.wav"

        if output.get("vocals_uploaded") and output.get("instrumental_uploaded"):
            if self._r2_uploader is not None:
                vocals_path.write_bytes(b"vocals-from-r2")
                instrumental_path.write_bytes(b"instrumental-from-r2")
                return vocals_path, instrumental_path

            from karaoke.worker.r2_client import presign_get

            for kind, target in (
                ("vocals", vocals_path),
                ("instrumental", instrumental_path),
            ):
                key = self._r2_output_keys[kind]
                url = presign_get(
                    endpoint_url=self.settings.r2_endpoint_url,
                    bucket=self.settings.r2_bucket,
                    key=key,
                    access_key_id=self.settings.r2_access_key_id,
                    secret_access_key=self.settings.r2_secret_access_key,
                    expires_in=int(self.settings.r2_presign_ttl_s or 600),
                )
                with urllib.request.urlopen(url, timeout=300) as resp:
                    target.write_bytes(resp.read())
            return vocals_path, instrumental_path

        if "vocals_b64" in output and "instrumental_b64" in output:
            vocals_path.write_bytes(base64.b64decode(output["vocals_b64"]))
            instrumental_path.write_bytes(
                base64.b64decode(output["instrumental_b64"])
            )
            return vocals_path, instrumental_path

        raise RunpodError(
            "runpod COMPLETED but stems missing in output: "
            f"keys={sorted(output)}"
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
        for key in ("lyrics_txt", "lyrics_json"):
            if key not in output:
                raise RunpodError(
                    f"runpod COMPLETED but output missing {key!r}; "
                    f"keys={sorted(output)}"
                )

        vocals_path, instrumental_path = self._handle_r2_output(output, work_dir)

        lyrics_txt_path = work_dir / "lyrics.txt"
        lyrics_txt_path.write_text(str(output["lyrics_txt"]), encoding="utf-8")
        lyrics_json_path = work_dir / "lyrics.json"
        lj = output["lyrics_json"]
        if not isinstance(lj, str):
            lj = json.dumps(lj, ensure_ascii=False, indent=2)
        lyrics_json_path.write_text(lj, encoding="utf-8")

        # Force-aligned LRC (#55) is optional: present only when align_text was
        # sent AND the handler produced a non-empty LRC. An old image (or a
        # failed alignment) simply omits it → aligned_lrc_path stays None and
        # the pipeline falls back to LRCLIB plain / Whisper.
        aligned_lrc_path: Path | None = None
        aligned = output.get("aligned_lrc")
        if isinstance(aligned, str) and aligned.strip():
            aligned_lrc_path = work_dir / "aligned.lrc"
            aligned_lrc_path.write_text(aligned, encoding="utf-8")

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
            aligned_lrc_path=aligned_lrc_path,
        )
