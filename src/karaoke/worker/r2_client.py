"""S3-compatible upload + presigned-URL generation for the RunPod path.

Used to bypass RunPod's ~10MB /run body cap by uploading the normalised
mix.wav to Cloudflare R2 and passing only a short-lived presigned GET
URL to the worker.

Pure stdlib (urllib + hmac + hashlib) — no `boto3` dep. Implements just
the SigV4 PUT and the presigned GET we actually need.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import quote


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    k_date = _hmac(("AWS4" + secret).encode("utf-8"), date)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


# R2 answers a burst with HTTP 429 (and the odd 5xx); one job failed on it
# mid-GPU-stage on 2026-11-24. Both calls are idempotent (PUT of the same
# bytes to a fresh key, GET of a presigned URL), so retry a few times.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRY_ATTEMPTS = 3
_RETRY_MAX_DELAY_S = 10.0


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    try:
        retry_after = float(exc.headers.get("Retry-After", ""))
    except (AttributeError, TypeError, ValueError):
        retry_after = 0.0
    return min(_RETRY_MAX_DELAY_S, max(retry_after, 2.0 * 2**attempt))


def urlopen_retry(request: urllib.request.Request | str, *, timeout: float, sleep=time.sleep):
    """``urllib.request.urlopen`` with a bounded retry on 429/5xx."""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRY_STATUSES or attempt == _RETRY_ATTEMPTS - 1:
                raise
            exc.close()
            sleep(_retry_delay(exc, attempt))
    raise AssertionError("unreachable")


def _split_endpoint(endpoint_url: str) -> tuple[str, str]:
    """Return (host, scheme) from e.g. ``https://<acct>.r2.cloudflarestorage.com``."""
    p = urllib.parse.urlsplit(endpoint_url)
    return p.netloc, p.scheme or "https"


def upload_object(
    *,
    endpoint_url: str,
    bucket: str,
    key: str,
    body: bytes,
    access_key_id: str,
    secret_access_key: str,
    region: str = "auto",
    content_type: str = "application/octet-stream",
) -> None:
    """SigV4 PUT to ``{endpoint}/{bucket}/{key}``. Raises on non-2xx."""
    host, scheme = _split_endpoint(endpoint_url)
    now = dt.datetime.now(dt.UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    canonical_uri = f"/{bucket}/{quote(key, safe='/-_.')}"
    payload_hash = _sha256(body)
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = (
        f"PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{_sha256(canonical_request.encode())}"
    )
    signing_key = _signing_key(secret_access_key, date_stamp, region, "s3")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    url = f"{scheme}://{host}{canonical_uri}"
    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Host": host,
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
            "Authorization": authorization,
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        },
    )
    with urlopen_retry(req, timeout=300) as resp:
        if resp.status not in (200, 201, 204):
            raise RuntimeError(f"R2 PUT {url} returned HTTP {resp.status}")


def presign_get(
    *,
    endpoint_url: str,
    bucket: str,
    key: str,
    access_key_id: str,
    secret_access_key: str,
    region: str = "auto",
    expires_in: int = 600,
) -> str:
    """Return a SigV4 presigned URL for ``GET {endpoint}/{bucket}/{key}``."""
    host, scheme = _split_endpoint(endpoint_url)
    now = dt.datetime.now(dt.UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    canonical_uri = f"/{bucket}/{quote(key, safe='/-_.')}"
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"

    qs = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key_id}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_in),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_qs = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(qs.items())
    )
    canonical_headers = f"host:{host}\n"
    signed_headers = "host"
    payload_hash = "UNSIGNED-PAYLOAD"
    canonical_request = (
        f"GET\n{canonical_uri}\n{canonical_qs}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{_sha256(canonical_request.encode())}"
    )
    signing_key = _signing_key(secret_access_key, date_stamp, region, "s3")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{scheme}://{host}{canonical_uri}?{canonical_qs}&X-Amz-Signature={signature}"


def presign_put(
    *,
    endpoint_url: str,
    bucket: str,
    key: str,
    access_key_id: str,
    secret_access_key: str,
    region: str = "auto",
    expires_in: int = 600,
) -> str:
    """Return a SigV4 presigned URL for ``PUT {endpoint}/{bucket}/{key}``."""
    host, scheme = _split_endpoint(endpoint_url)
    now = dt.datetime.now(dt.UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    canonical_uri = f"/{bucket}/{quote(key, safe='/-_.')}"
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    qs = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key_id}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_in),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_qs = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(qs.items())
    )
    canonical_headers = f"host:{host}\n"
    signed_headers = "host"
    payload_hash = "UNSIGNED-PAYLOAD"
    canonical_request = (
        f"PUT\n{canonical_uri}\n{canonical_qs}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{_sha256(canonical_request.encode())}"
    )
    signing_key = _signing_key(secret_access_key, date_stamp, region, "s3")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{scheme}://{host}{canonical_uri}?{canonical_qs}&X-Amz-Signature={signature}"


def upload_file(
    path: Path,
    *,
    endpoint_url: str,
    bucket: str,
    key: str,
    access_key_id: str,
    secret_access_key: str,
    region: str = "auto",
    content_type: str = "application/octet-stream",
) -> None:
    upload_object(
        endpoint_url=endpoint_url,
        bucket=bucket,
        key=key,
        body=path.read_bytes(),
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
        content_type=content_type,
    )
