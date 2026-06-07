"""Small Valkey/Redis FIFO adapter for the coordinator skeleton.

The project does not need a full Redis client dependency for the API skeleton:
we only need a health ping and one enqueue operation.  This module speaks the
RESP wire protocol directly and degrades cleanly when Redis is absent in local
dev/test.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import urlparse


class QueueError(RuntimeError):
    """Raised when Valkey/Redis is unreachable or returns an error."""


@dataclass(frozen=True)
class RedisQueue:
    """Minimal FIFO queue backed by Valkey/Redis lists."""

    url: str
    queue_key: str = "karaoke:jobs"
    running_key: str = "karaoke:running"
    timeout: float = 0.5

    def ping(self) -> bool:
        """Return true when Redis responds to PING."""
        try:
            resp = self._command("PING")
        except QueueError:
            return False
        return resp == "PONG"

    def enqueue(self, job_id: int) -> None:
        """Append a job id to the FIFO queue."""
        self._command("RPUSH", self.queue_key, str(job_id))

    def _command(self, *parts: str) -> object:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"redis", "rediss"}:
            raise QueueError(f"unsupported Redis URL scheme: {parsed.scheme or '<empty>'}")
        if parsed.scheme == "rediss":
            raise QueueError("rediss is not supported by the skeleton queue adapter")

        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6379
        db = (parsed.path or "/0").lstrip("/") or "0"
        password = parsed.password

        try:
            with socket.create_connection((host, port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                if password:
                    _read_resp(sock, _encode_command("AUTH", password))
                if db != "0":
                    _read_resp(sock, _encode_command("SELECT", db))
                return _read_resp(sock, _encode_command(*parts))
        except OSError as exc:
            raise QueueError(str(exc)) from exc


def _encode_command(*parts: str) -> bytes:
    out = [f"*{len(parts)}\r\n".encode()]
    for part in parts:
        raw = part.encode("utf-8")
        out.append(f"${len(raw)}\r\n".encode())
        out.append(raw + b"\r\n")
    return b"".join(out)


def _read_line(sock: socket.socket) -> bytes:
    data = bytearray()
    while not data.endswith(b"\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            raise QueueError("Redis closed the connection")
        data.extend(chunk)
    return bytes(data[:-2])


def _read_resp(sock: socket.socket, request: bytes) -> object:
    sock.sendall(request)
    prefix = sock.recv(1)
    if not prefix:
        raise QueueError("Redis returned an empty response")

    if prefix == b"+":
        return _read_line(sock).decode("utf-8", errors="replace")
    if prefix == b"-":
        raise QueueError(_read_line(sock).decode("utf-8", errors="replace"))
    if prefix == b":":
        return int(_read_line(sock))
    if prefix == b"$":
        length = int(_read_line(sock))
        if length < 0:
            return None
        data = b""
        while len(data) < length + 2:
            chunk = sock.recv(length + 2 - len(data))
            if not chunk:
                raise QueueError("Redis closed the bulk response")
            data += chunk
        return data[:length].decode("utf-8", errors="replace")
    raise QueueError(f"unsupported Redis response prefix: {prefix!r}")
