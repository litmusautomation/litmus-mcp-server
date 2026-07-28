"""Tests for the legacy HTTP+SSE transport's message-posting route.

The stream's opening "endpoint" event tells the client where to POST its
requests. That path has to match the mounted route exactly: mounting
"/messages" while advertising "/messages" makes Starlette answer the POST with
a 307 to "/messages/", and a client that does not replay a POST across a
redirect never delivers a single message. Current clients use /mcp, so this is
only reachable by older ones, which is exactly why it needs a guard.
"""

import asyncio

import httpx
from starlette.routing import Mount

from server import app, sse


def run(coro):
    return asyncio.run(coro)


def _mount_paths() -> list[str]:
    return [r.path for r in app.routes if isinstance(r, Mount)]


def test_advertised_endpoint_matches_the_mounted_path():
    """The invariant that broke: advertised path vs mounted path.

    Starlette normalizes a Mount path to its un-slashed form, so the two are
    compared without the trailing slash.
    """
    advertised = sse._endpoint
    assert advertised.rstrip("/") in _mount_paths(), (
        f"SSE advertises {advertised!r} but the mounted paths are "
        f"{_mount_paths()!r}; a mismatch turns every client POST into a 307"
    )


def test_endpoint_path_has_a_trailing_slash():
    """Starlette serves the slashed form and redirects the un-slashed one."""
    assert sse._endpoint.endswith("/")


def test_posting_to_the_advertised_path_is_not_redirected():
    """A POST to the advertised path must be served, not answered with a 307.

    No session_id is supplied, so a rejection is expected; what matters is
    that the rejection comes from the message handler rather than from
    Starlette's redirect.
    """
    async def post():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=False
        ) as client:
            return await client.post(
                sse._endpoint, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
            )

    response = run(post())

    assert response.status_code != 307, (
        "the advertised endpoint redirects, so clients that do not replay "
        "POST bodies across redirects cannot send messages"
    )
    assert response.status_code < 500
