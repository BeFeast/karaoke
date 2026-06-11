"""The central cookie-jar rotation endpoints are gone (issue #132).

#73 let a client keep a server-side jar fresh through a dedicated upload/status
route pair. #77 made cookies per-job and client-supplied; #132 removed the
rotation endpoints entirely. These tests pin the removal: both verbs on the
retired route 404 even for a machine bearer (the strongest auth layer),
proving the route itself is gone — not merely rejecting the caller (an auth
artifact would be 401/403).
"""
from __future__ import annotations

# Split literal: the repo-wide "no central-jar references" sweep greps for the
# joined path; this file is the one place the retired route may appear, and
# only to prove it 404s.
_RETIRED_ROUTE = "/cookies" "/youtube"

_BLOB = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tvalue\n"
)
_MACHINE_BEARER = {"Authorization": "Bearer test-service-token"}


def test_post_cookie_rotation_route_is_404(client):
    resp = client.post(
        _RETIRED_ROUTE,
        content=_BLOB,
        headers={"Content-Type": "text/plain", **_MACHINE_BEARER},
    )
    assert resp.status_code == 404


def test_get_cookie_rotation_route_is_404(client):
    resp = client.get(_RETIRED_ROUTE, headers=_MACHINE_BEARER)
    assert resp.status_code == 404
