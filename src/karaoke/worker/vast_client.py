"""vast.ai lifecycle + karaoke GPU calls.

Ported faithfully from scribe's ``pipeline/whisper_client.py``. The lifecycle
(offer selection, instance create/destroy, ssh waits, budget caps) is the same;
the *work* differs: instead of scp-ing a remote script and running whisper
in-process, the karaoke GPU image exposes an HTTP service on ``:8000`` with
``/demucs`` and ``/whisper`` endpoints. The coordinator reaches it by opening an
SSH local-forward (``ssh -L <localport>:localhost:8000``) to the instance and
POSTing wavs to ``http://localhost:<localport>``.

THE MOST IMPORTANT SAFETY PROPERTY: the vast instance is **always** destroyed in
a ``finally`` clause — on success, on any GPU/HTTP error, and on budget overrun.
Do not regress this.

Budget rails honored here:
- ``vast_max_job_cost`` — per-job USD ceiling (cost-budget deadline).
- ``MAX_INSTANCE_SECONDS`` (= 1800) — hard wall-clock upper bound per instance.
- ``vast_daily_cost_cap`` — rolling 24h USD ceiling; the job is REFUSED (no
  instance is provisioned) when the prior-24h spend would breach it.
"""
from __future__ import annotations

import contextlib
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

VAST_API = "https://console.vast.ai/api/v0"

# Hard upper bound on a single instance's wall-clock budget; the per-job cost
# guard (vast_max_job_cost) usually trips well before this.
MAX_INSTANCE_SECONDS = 1800

# Vast instance status fields that mean the container will not become ready —
# fail fast instead of polling for the full ready_timeout window.
_VAST_FAILED_STATES = frozenset(
    {"exited", "failed", "crashed", "offline", "error", "stopped"}
)


class VastError(RuntimeError):
    """Any vast.ai lifecycle or GPU-call failure."""


class VastBudgetError(VastError):
    """A budget cap (per-job or daily) was breached; the job is refused/aborted."""


class VastInstanceFailedError(VastError):
    """Vast container reached a terminal-failure state during startup."""


class VastReadyTimeoutError(VastError):
    """Vast container did not become ready within the per-attempt budget."""


@dataclass
class GpuJobResult:
    """Outcome of a single GPU window (one instance, demucs + whisper)."""

    vast_instance_id: int | str
    vast_cost: float
    gpu_model: str
    vocals_path: Path
    instrumental_path: Path
    lyrics_txt_path: Path
    lyrics_json_path: Path
    # Optional synced LRC produced by in-GPU force-alignment of supplied plain
    # lyrics against the vocal stem (#55). ``None`` when no ``align_text`` was
    # sent, or the handler/aligner produced nothing (old image, or failure).
    aligned_lrc_path: Path | None = None


# --------------------------------------------------------------------------
# subprocess + http helpers
# --------------------------------------------------------------------------
def _run(
    cmd: list[str], *, check: bool = True, timeout: int | None = None
) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if check:
            raise VastError(
                f"command timed out after {timeout}s: {' '.join(cmd)}"
            ) from exc
        return subprocess.CompletedProcess(
            cmd, 124, stdout=exc.stdout or "", stderr=f"timeout after {timeout}s"
        )
    if check and proc.returncode != 0:
        raise VastError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _vast(
    api_key: str,
    method: str,
    path: str,
    payload: dict | None = None,
    timeout: int = 45,
) -> dict:
    """Single vast.ai REST call. Mirrors scribe's ``_vast`` (urllib, Bearer)."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{VAST_API}{path}", data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise VastError(f"Vast API {method} {path}: HTTP {exc.code}: {detail}") from exc
    return json.loads(body) if body.strip() else {}


# --------------------------------------------------------------------------
# ssh key
# --------------------------------------------------------------------------
def _ensure_local_ssh_key() -> tuple[Path, str]:
    key = Path.home() / ".ssh" / "id_ed25519"
    pub = key.with_suffix(".pub")
    if not key.is_file() or not pub.is_file():
        key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _run(
            [
                "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key),
                "-C", "karaoke-vast",
            ]
        )
    return key, pub.read_text(encoding="utf-8").strip()


def _ensure_vast_ssh_key(api_key: str, public_key: str) -> None:
    try:
        if public_key in json.dumps(_vast(api_key, "GET", "/ssh/")):
            return
    except Exception:
        pass
    try:
        _vast(api_key, "POST", "/ssh/", {"ssh_key": public_key})
    except VastError as exc:
        if "already exists" not in str(exc):
            raise


# --------------------------------------------------------------------------
# offers
# --------------------------------------------------------------------------
def _select_offers(
    api_key: str,
    *,
    max_price: float,
    gpu_regex: str,
    min_cuda: float,
    excluded_hosts: set[int] | None = None,
) -> list[dict]:
    excluded = excluded_hosts or set()
    query = {
        "limit": 400,
        "type": "on-demand",
        "rentable": {"eq": True},
        "rented": {"eq": False},
        "verified": {"eq": True},
        "gpu_ram": {"gte": 16000},
        "num_gpus": {"eq": 1},
    }
    offers = _vast(api_key, "POST", "/bundles/", query, timeout=60).get("offers", [])
    pattern = re.compile(gpu_regex, re.IGNORECASE)
    candidates = []
    for offer in offers:
        price = float(offer.get("dph_total") or 999)
        cuda = float(offer.get("cuda_max_good") or 0)
        reliability = float(
            offer.get("reliability") or offer.get("reliability2") or 0
        )
        host_id_raw = offer.get("host_id")
        try:
            host_id = int(host_id_raw) if host_id_raw is not None else None
        except (TypeError, ValueError):
            host_id = None
        if host_id is not None and host_id in excluded:
            continue
        if (
            price <= max_price
            and cuda >= min_cuda
            and reliability >= 0.90
            and pattern.search(str(offer.get("gpu_name") or ""))
        ):
            candidates.append(offer)
    if not candidates:
        raise VastError(
            f"no Vast offer matched (max_price={max_price}, "
            f"cuda_max_good>={min_cuda}, gpu_regex, reliability>=0.90)"
        )
    # Cheapest first; prefer high reliability and a fast network on ties so the
    # CUDA image pull does not eat the ready-timeout budget.
    return sorted(
        candidates,
        key=lambda o: (
            float(o.get("dph_total") or 999),
            -float(o.get("reliability") or o.get("reliability2") or 0),
            -float(o.get("inet_down") or 0),
        ),
    )


def _is_no_such_ask(exc: BaseException) -> bool:
    """Offer→ask race: Vast returns HTTP 400 'no_such_ask' / 'not available'
    when the offer was rented by another tenant between ``_select_offers`` and
    our ``PUT /asks/{id}``. We can immediately try the next candidate."""
    text = str(exc)
    if "HTTP 400" not in text:
        return False
    lowered = text.lower()
    return "no_such_ask" in lowered or "not available" in lowered


# --------------------------------------------------------------------------
# instance lifecycle
# --------------------------------------------------------------------------
def _create_instance(api_key: str, offer: dict, image: str, public_key: str) -> int:
    label = f"{socket.gethostname()}-karaoke-vast-" + time.strftime(
        "%Y%m%dT%H%M%SZ", time.gmtime()
    )
    # The image already bundles the HTTP server + Demucs + faster-whisper and
    # starts it via its entrypoint; the onstart marker just lets us detect
    # readiness over SSH before we open the tunnel.
    onstart = (
        "set -eu; "
        'export PATH="/usr/local/bin:/root/.local/bin:/opt/conda/bin:$PATH"; '
        "echo ready >/root/karaoke-ready"
    )
    payload = {
        "client_id": "me",
        "image": image,
        "env": {},
        "price": None,
        "disk": 30,
        "label": label,
        "extra": None,
        "onstart": onstart,
        "image_login": None,
        "python_utf8": False,
        "lang_utf8": False,
        "use_jupyter_lab": False,
        "jupyter_dir": None,
        "force": False,
        "cancel_unavail": True,
        "template_hash_id": None,
        "user": None,
        "runtype": "ssh_direc ssh_proxy",
    }
    resp = _vast(api_key, "PUT", f"/asks/{offer['id']}/", payload, timeout=60)
    iid = resp.get("new_contract") or resp.get("id") or resp.get("instance_id")
    if not iid:
        raise VastError(f"Vast create response missing instance id: {resp}")
    with contextlib.suppress(Exception):
        _vast(api_key, "POST", f"/instances/{iid}/ssh/", {"ssh_key": public_key})
    return int(iid)


def _destroy_instance(api_key: str, instance_id: int) -> None:
    _vast(api_key, "DELETE", f"/instances/{instance_id}/", {}, timeout=45)
    try:
        confirm = _vast(api_key, "GET", f"/instances/{instance_id}/", timeout=45)
    except VastError as exc:
        if "HTTP 404" in str(exc):
            return
        raise
    if confirm.get("instances") is None:
        return
    raise VastError(
        f"Vast instance {instance_id} still present after destroy: {confirm}"
    )


def _get_instance(api_key: str, instance_id: int) -> dict:
    for inst in _vast(api_key, "GET", "/instances/", timeout=45).get("instances", []):
        if int(inst.get("id") or 0) == instance_id:
            return inst
    return {}


# --------------------------------------------------------------------------
# ssh helpers
# --------------------------------------------------------------------------
def _ssh_base(host: str, port: int, key_path: Path) -> list[str]:
    return [
        "ssh", "-q", "-i", str(key_path),
        "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=4",
        "-p", str(port), f"root@{host}",
    ]


def _ssh_endpoints(instance: dict) -> list[tuple[str, int, str]]:
    endpoints: list[tuple[str, int, str]] = []
    public_ip = str(instance.get("public_ipaddr") or "").strip()
    ports = instance.get("ports") or {}
    ssh_ports = ports.get("22/tcp") if isinstance(ports, dict) else None
    if public_ip and isinstance(ssh_ports, list):
        for item in ssh_ports:
            if (
                isinstance(item, dict)
                and item.get("HostPort")
                and str(item.get("HostIp") or "") != "::"
            ):
                endpoints.append((public_ip, int(item["HostPort"]), "direct"))
    if instance.get("ssh_host") and instance.get("ssh_port"):
        endpoints.append(
            (str(instance["ssh_host"]), int(instance["ssh_port"]), "proxy")
        )
    seen: set[tuple[str, int]] = set()
    unique: list[tuple[str, int, str]] = []
    for host, port, kind in endpoints:
        if (host, port) not in seen:
            seen.add((host, port))
            unique.append((host, port, kind))
    return unique


# --------------------------------------------------------------------------
# budget + waits
# --------------------------------------------------------------------------
def _budget_deadline(
    started: float, price: float, max_cost: float, max_seconds: int
) -> float:
    by_cost = max_cost / price * 3600 if price > 0 else max_seconds
    return started + min(max_seconds, by_cost)


def _ensure_budget(
    started: float,
    deadline: float,
    price: float,
    max_cost: float,
    *,
    ready_timeout: float | None = None,
    label: str = "",
) -> None:
    """Raise when the per-attempt deadline is exceeded."""
    if time.monotonic() <= deadline:
        return
    elapsed = time.monotonic() - started
    suffix = f" ({label})" if label else ""
    if ready_timeout is not None and elapsed >= ready_timeout:
        raise VastReadyTimeoutError(
            f"Vast ready_timeout exceeded after {elapsed:.0f}s "
            f"(cap {ready_timeout:.0f}s){suffix}"
        )
    raise VastBudgetError(
        f"Vast budget guard tripped after {elapsed:.0f}s "
        f"(~${price * elapsed / 3600:.4f}, cap ${max_cost}){suffix}"
    )


def _vast_failure_state(info: dict) -> str | None:
    actual = str(info.get("actual_status") or "").lower()
    cur = str(info.get("cur_state") or "").lower()
    intended = str(info.get("intended_status") or "").lower()
    for value in (actual, cur, intended):
        if value and value in _VAST_FAILED_STATES:
            return value
    return None


def _format_failure_detail(info: dict, failure_state: str) -> str:
    actual = str(info.get("actual_status") or "").lower()
    cur = str(info.get("cur_state") or "").lower()
    msg = str(info.get("status_msg") or "").strip()
    parts = [
        f"failure_state={failure_state}",
        f"actual_status={actual or '?'}",
        f"cur_state={cur or '?'}",
    ]
    if msg:
        snippet = msg.replace("\n", " ").strip()[:240]
        parts.append(f"status_msg={snippet!r}")
    return ", ".join(parts)


def _wait_for_ssh(
    api_key, instance_id, key_path, started, deadline, price, max_cost,
    *, ready_timeout: float, label: str = "",
) -> tuple[str, int]:
    while True:
        _ensure_budget(
            started, deadline, price, max_cost,
            ready_timeout=ready_timeout, label=label,
        )
        info = _get_instance(api_key, instance_id)
        failure = _vast_failure_state(info)
        if failure is not None:
            raise VastInstanceFailedError(
                f"Vast container failed to start: "
                f"{_format_failure_detail(info, failure)}"
            )
        states = {
            str(info.get("actual_status") or "").lower(),
            str(info.get("cur_state") or "").lower(),
        }
        if "running" in states:
            for host, port, kind in _ssh_endpoints(info):
                if (
                    _run([*_ssh_base(host, port, key_path), "true"],
                         check=False, timeout=45).returncode
                    == 0
                ):
                    print(
                        f"Using Vast {kind} SSH endpoint {host}:{port}",
                        file=sys.stderr,
                    )
                    return host, port
        time.sleep(10)


def _wait_remote_ready(
    api_key, instance_id, host, port, key_path, started, deadline, price, max_cost,
    *, ready_timeout: float, label: str = "",
) -> None:
    # The image's HTTP server answers /health once Demucs + whisper deps are
    # importable; we gate on the onstart marker + a local /health probe via SSH.
    check = (
        "test -f /root/karaoke-ready && "
        "curl -fsS http://localhost:8000/health >/dev/null"
    )
    while True:
        _ensure_budget(
            started, deadline, price, max_cost,
            ready_timeout=ready_timeout, label=label,
        )
        info = _get_instance(api_key, instance_id)
        failure = _vast_failure_state(info)
        if failure is not None:
            raise VastInstanceFailedError(
                f"Vast container failed mid-startup: "
                f"{_format_failure_detail(info, failure)}"
            )
        if (
            _run([*_ssh_base(host, port, key_path), check], check=False, timeout=45).returncode
            == 0
        ):
            return
        time.sleep(10)


# --------------------------------------------------------------------------
# SSH local-forward tunnel + GPU HTTP calls
# --------------------------------------------------------------------------
def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _SshForward:
    """Context manager owning an ``ssh -L <local>:localhost:8000`` tunnel."""

    def __init__(self, host: str, port: int, key_path: Path, remote_port: int = 8000):
        self.host = host
        self.port = port
        self.key_path = key_path
        self.remote_port = remote_port
        self.local_port = _free_local_port()
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> _SshForward:
        cmd = [
            "ssh", "-q", "-i", str(self.key_path),
            "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4", "-o", "ExitOnForwardFailure=yes",
            "-N",
            "-L", f"{self.local_port}:localhost:{self.remote_port}",
            "-p", str(self.port), f"root@{self.host}",
        ]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self._wait_tunnel_up()
        return self

    def _wait_tunnel_up(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise VastError("ssh -L tunnel process exited before becoming ready")
            with contextlib.suppress(OSError), socket.create_connection(
                ("127.0.0.1", self.local_port), timeout=2
            ):
                return
            time.sleep(0.5)
        raise VastError(f"ssh -L tunnel to {self.host}:{self.port} never came up")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def __exit__(self, *exc) -> None:
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self._proc.wait(timeout=10)
            if self._proc.poll() is None:
                with contextlib.suppress(Exception):
                    self._proc.kill()


def _post_zip(base_url: str, endpoint: str, wav: Path, out_dir: Path, timeout: float) -> list[Path]:
    """POST a wav to ``{base_url}{endpoint}`` and unpack the returned zip into
    ``out_dir``. Returns the list of extracted file paths."""
    with wav.open("rb") as fh:
        files = {"file": (wav.name, fh, "audio/wav")}
        resp = httpx.post(f"{base_url}{endpoint}", files=files, timeout=timeout)
    if resp.status_code != 200:
        raise VastError(
            f"GPU {endpoint} returned HTTP {resp.status_code}: "
            f"{resp.text[:500]}"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{endpoint.strip('/')}.zip"
    zip_path.write_bytes(resp.content)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            # Flatten to the basename to avoid zip-slip and nested dirs.
            safe = Path(name).name
            if not safe:
                continue
            target = out_dir / safe
            with zf.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(target)
    return extracted


# --------------------------------------------------------------------------
# public client
# --------------------------------------------------------------------------
class VastClient:
    """Provisions an instance, runs Demucs + Whisper over the tunnel, and
    ALWAYS destroys the instance in ``finally``.

    The injection seams below exist so unit tests can mock the network without
    ever touching vast.ai or opening a real tunnel:
      - ``vast_call`` wraps :func:`_vast`
      - ``destroy_fn`` wraps :func:`_destroy_instance`
      - ``forward_cls`` wraps :class:`_SshForward`
      - ``post_zip`` wraps :func:`_post_zip`
    """

    def __init__(
        self,
        settings,
        *,
        prior_24h_cost_micros: int = 0,
        vast_call: Callable[..., dict] = _vast,
        destroy_fn: Callable[[str, int], None] = _destroy_instance,
        forward_cls: type[_SshForward] = _SshForward,
        post_zip: Callable[..., list[Path]] = _post_zip,
        on_instance_created: Callable[[int], None] | None = None,
    ) -> None:
        self.settings = settings
        self.prior_24h_cost_micros = prior_24h_cost_micros
        self._vast = vast_call
        self._destroy = destroy_fn
        self._forward_cls = forward_cls
        self._post_zip = post_zip
        self._on_instance_created = on_instance_created

    # -- budget --------------------------------------------------------------
    def _check_daily_cap(self) -> None:
        cap = float(self.settings.vast_daily_cost_cap or 0)
        if cap <= 0:
            return
        spent = self.prior_24h_cost_micros / 1_000_000
        # Refuse if we're already at/over the cap, or a single max-cost job would
        # push us past it — we cannot know the true cost up front, so use the
        # per-job ceiling as the conservative worst case.
        projected = spent + float(self.settings.vast_max_job_cost or 0)
        if spent >= cap or projected > cap:
            raise VastBudgetError(
                f"daily vast cost cap reached: spent ${spent:.4f} "
                f"+ max-job ${self.settings.vast_max_job_cost} > cap ${cap}"
            )

    # -- main entrypoint -----------------------------------------------------
    def run(
        self,
        vocals_input_wav: Path,
        work_dir: Path,
        *,
        align_text: str | None = None,
        align_lang: str | None = None,
    ) -> GpuJobResult:
        """Provision an instance and run /demucs then /whisper against it.

        ``vocals_input_wav`` is the normalized full-mix wav the coordinator
        produced; the GPU image's /demucs splits it into vocals + instrumental,
        and /whisper transcribes the *vocals* stem. The instance is destroyed in
        ``finally`` no matter what.

        ``align_text``/``align_lang`` are accepted for interface parity with the
        RunPod worker (#55), but the vast.ai HTTP image (``/demucs`` + ``/whisper``)
        does not implement force-alignment; they are ignored here and the result
        carries no ``aligned_lrc_path``. vast.ai is the fallback runtime — RunPod
        is the alignment-capable path.
        """
        _ = (align_text, align_lang)  # accepted, unused on the vast path
        api_key = self.settings.vast_api_key.strip()
        if not api_key:
            raise VastError("KARAOKE_VAST_API_KEY is not set")

        # Daily cap is checked BEFORE provisioning anything.
        self._check_daily_cap()

        max_price = float(self.settings.vast_max_price_per_hour)
        min_cuda = float(self.settings.vast_min_cuda)
        max_job_cost = float(self.settings.vast_max_job_cost)
        ready_timeout = int(self.settings.vast_instance_ready_timeout)
        offer_attempts = max(1, int(self.settings.vast_offer_attempts))
        image = self.settings.vast_image

        key_path, public_key = _ensure_local_ssh_key()
        # Use the same urllib path via the injectable seam where practical, but
        # ssh-key registration is harmless idempotent; reuse the module helper.
        self._ensure_vast_ssh_key(api_key, public_key)

        offers = _select_offers(
            api_key,
            max_price=max_price,
            gpu_regex=self.settings.vast_gpu_regex,
            min_cuda=min_cuda,
        )

        started = time.monotonic()
        instance_id: int | None = None
        host = port = None
        price = 0.0
        gpu_model = ""
        deadline = started + MAX_INSTANCE_SECONDS
        last_err: Exception | None = None
        attempts = 0
        excluded_hosts: set[int] = set()

        try:
            for offer in offers:
                if attempts >= offer_attempts:
                    break
                host_id_raw = offer.get("host_id")
                try:
                    offer_host_id: int | None = (
                        int(host_id_raw) if host_id_raw is not None else None
                    )
                except (TypeError, ValueError):
                    offer_host_id = None
                if offer_host_id is not None and offer_host_id in excluded_hosts:
                    continue
                offer_label = f"offer_id={offer.get('id')} host_id={offer_host_id}"
                price = float(offer.get("dph_total") or 0)
                gpu_model = str(offer.get("gpu_name") or "")
                deadline = _budget_deadline(
                    started, price, max_job_cost, MAX_INSTANCE_SECONDS
                )
                try:
                    instance_id = self._create_instance(
                        api_key, offer, image, public_key
                    )
                except (VastError, TimeoutError) as exc:
                    last_err = exc
                    if _is_no_such_ask(exc):
                        instance_id = None
                        continue
                    attempts += 1
                    print(
                        f"Warning: Vast offer {offer.get('id')} unusable: {exc}",
                        file=sys.stderr,
                    )
                    instance_id = None
                    continue
                attempts += 1
                try:
                    if self._on_instance_created is not None:
                        self._on_instance_created(instance_id)
                    startup_deadline = min(deadline, time.monotonic() + ready_timeout)
                    host, port = _wait_for_ssh(
                        api_key, instance_id, key_path, started, startup_deadline,
                        price, max_job_cost,
                        ready_timeout=ready_timeout, label=offer_label,
                    )
                    _wait_remote_ready(
                        api_key, instance_id, host, port, key_path, started,
                        startup_deadline, price, max_job_cost,
                        ready_timeout=ready_timeout, label=offer_label,
                    )
                    break
                except (VastError, TimeoutError) as exc:
                    last_err = exc
                    print(
                        f"Warning: Vast offer {offer.get('id')} unusable: {exc} "
                        f"({offer_label})",
                        file=sys.stderr,
                    )
                    if offer_host_id is not None:
                        excluded_hosts.add(offer_host_id)
                    if instance_id is not None:
                        # Destroy this dead instance immediately before the next
                        # attempt (the outer finally only owns the last one).
                        with contextlib.suppress(Exception):
                            self._destroy(api_key, instance_id)
                        instance_id = None
                    host = port = None

            if instance_id is None or host is None:
                raise VastError(
                    f"no Vast instance became ready; last error: {last_err}; "
                    f"blacklisted host_ids="
                    f"{sorted(excluded_hosts) if excluded_hosts else '[]'}"
                )

            work_dir.mkdir(parents=True, exist_ok=True)
            _ensure_budget(started, deadline, price, max_job_cost)
            remote_timeout = max(120, int(deadline - time.monotonic()))

            with self._forward_cls(host, port, key_path) as fwd:
                base_url = fwd.base_url
                # /demucs: full-mix wav -> vocals.wav + instrumental.wav
                demucs_files = self._post_zip(
                    base_url, "/demucs", vocals_input_wav, work_dir, remote_timeout
                )
                vocals = _find(demucs_files, "vocals.wav")
                instrumental = _find(demucs_files, "instrumental.wav")
                if vocals is None or instrumental is None:
                    raise VastError(
                        f"/demucs zip missing vocals/instrumental: "
                        f"{[p.name for p in demucs_files]}"
                    )

                _ensure_budget(started, deadline, price, max_job_cost)
                remote_timeout = max(120, int(deadline - time.monotonic()))
                # /whisper: transcribe the SEPARATED vocals stem.
                whisper_files = self._post_zip(
                    base_url, "/whisper", vocals, work_dir, remote_timeout
                )
                lyrics_txt = _find(whisper_files, "lyrics.txt")
                lyrics_json = _find(whisper_files, "lyrics.json")
                if lyrics_txt is None or lyrics_json is None:
                    raise VastError(
                        f"/whisper zip missing lyrics.txt/lyrics.json: "
                        f"{[p.name for p in whisper_files]}"
                    )

            elapsed = time.monotonic() - started
            return GpuJobResult(
                vast_instance_id=instance_id,
                vast_cost=price * elapsed / 3600 if price else 0.0,
                gpu_model=gpu_model,
                vocals_path=vocals,
                instrumental_path=instrumental,
                lyrics_txt_path=lyrics_txt,
                lyrics_json_path=lyrics_json,
            )
        finally:
            # THE safety property: always destroy the (last) live instance.
            if instance_id is not None:
                with contextlib.suppress(Exception):
                    self._destroy(api_key, instance_id)

    # -- injectable thin wrappers (so tests can monkeypatch the instance) ----
    def _ensure_vast_ssh_key(self, api_key: str, public_key: str) -> None:
        try:
            if public_key in json.dumps(self._vast(api_key, "GET", "/ssh/")):
                return
        except Exception:
            pass
        try:
            self._vast(api_key, "POST", "/ssh/", {"ssh_key": public_key})
        except VastError as exc:
            if "already exists" not in str(exc):
                raise

    def _create_instance(
        self, api_key: str, offer: dict, image: str, public_key: str
    ) -> int:
        # Delegate to the module helper but through the injected vast seam so the
        # PUT /asks call is mockable. We re-implement the tiny envelope here.
        label = f"{socket.gethostname()}-karaoke-vast-" + time.strftime(
            "%Y%m%dT%H%M%SZ", time.gmtime()
        )
        onstart = (
            "set -eu; "
            'export PATH="/usr/local/bin:/root/.local/bin:/opt/conda/bin:$PATH"; '
            "echo ready >/root/karaoke-ready"
        )
        payload = {
            "client_id": "me", "image": image, "env": {}, "price": None,
            "disk": 30, "label": label, "extra": None, "onstart": onstart,
            "image_login": None, "python_utf8": False, "lang_utf8": False,
            "use_jupyter_lab": False, "jupyter_dir": None, "force": False,
            "cancel_unavail": True, "template_hash_id": None, "user": None,
            "runtype": "ssh_direc ssh_proxy",
        }
        resp = self._vast(api_key, "PUT", f"/asks/{offer['id']}/", payload, timeout=60)
        iid = resp.get("new_contract") or resp.get("id") or resp.get("instance_id")
        if not iid:
            raise VastError(f"Vast create response missing instance id: {resp}")
        with contextlib.suppress(Exception):
            self._vast(api_key, "POST", f"/instances/{iid}/ssh/", {"ssh_key": public_key})
        return int(iid)


def _find(paths: list[Path], name: str) -> Path | None:
    for p in paths:
        if p.name == name:
            return p
    return None
