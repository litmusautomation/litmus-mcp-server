"""Tests for the documentation resource surface (resources/list, resources/read).

Regression cover for the round-2 evaluation's blocking finding: every
advertised resource failed because the read handler passed an AnyUrl into a
str-keyed registry and TextContent objects into an SDK that wants str. Both
bugs were silent at the type level, so these tests drive the real MCP request
handler rather than the helper functions underneath it.

Network is mocked throughout; the markdown-preference logic is pinned against
Archbee's actual behaviour, including its habit of answering unknown ".md"
paths with HTTP 200 and an HTML page.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from mcp import types
from pydantic import AnyUrl

from tools import resource_tools
from tools.resource_tools import (
    DOCUMENTATION_RESOURCES,
    _markdown_url,
    _strip_frontmatter,
    fetch_documentation_content,
    get_documentation_resource_list,
    read_documentation_resource,
)


def run(coro):
    return asyncio.run(coro)


def _read_request(uri: str) -> types.ReadResourceRequest:
    return types.ReadResourceRequest(
        method="resources/read",
        params=types.ReadResourceRequestParams(uri=AnyUrl(uri)),
    )


def _fake_response(
    status: int = 200, content_type: str = "text/markdown", text: str = ""
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers={"content-type": f"{content_type}; charset=utf-8"},
        text=text,
        request=httpx.Request("GET", "https://docs.litmus.io/x"),
    )


# Bound before patching: the replacement factory has to build a real client,
# and looking the class up by name inside it would find the patch instead.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mocked_http(handler):
    """Patch httpx.AsyncClient so every request is served by `handler`."""

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    return patch("httpx.AsyncClient", factory)


MARKDOWN_PAGE = (
    "---\ntitle: DeviceHub\nslug: devicehub\n---\n\n"
    "DeviceHub connects industrial devices to Litmus Edge.\n\n"
    "## Drivers\n\nModbusTCP, OPCUA and BACnet are supported.\n" + "x" * 300
)

# Archbee serves this, with HTTP 200, for any ".md" path it does not know.
SPA_SHELL = '<!DOCTYPE html><html lang="en"><head><meta charSet="utf-8"/>' + "y" * 500


# ── the blocking defect ──────────────────────────────────────────────────────


@pytest.mark.parametrize("uri", list(DOCUMENTATION_RESOURCES))
def test_read_resource_returns_content_for_every_advertised_uri(uri):
    """Every resource resources/list advertises must be readable.

    Before the fix this raised a ValidationError for all of them: the SDK's
    str/bytes match fell through on a TextContent and put None in the result.
    """
    from server import mcp

    handler = mcp.request_handlers[types.ReadResourceRequest]
    with patch.object(
        resource_tools,
        "fetch_documentation_content",
        new=AsyncMock(return_value="fetched body"),
    ):
        result = run(handler(_read_request(uri)))

    contents = result.root.contents
    assert len(contents) == 1
    assert contents[0].text.strip()
    assert contents[0].mimeType == "text/markdown"


def test_read_resource_accepts_anyurl_not_just_str():
    """The handler receives a pydantic AnyUrl, and the registry is str-keyed.

    Without str(uri) every lookup missed and the unknown-resource message was
    returned as a successful read, which is worse than an error because it
    looks like content.
    """
    uri = "litmus://docs/edge/devicehub"
    with patch.object(
        resource_tools,
        "fetch_documentation_content",
        new=AsyncMock(return_value="fetched body"),
    ):
        via_str = run(read_documentation_resource(uri))
        via_anyurl = run(read_documentation_resource(str(AnyUrl(uri))))

    assert "Unknown documentation resource" not in via_str[0].text
    assert via_str[0].text == via_anyurl[0].text


def test_unknown_resource_lists_the_available_ones():
    result = run(read_documentation_resource("litmus://docs/nope"))
    text = result[0].text
    assert "Unknown documentation resource" in text
    assert "litmus://docs/edge/devicehub" in text


def test_advertised_list_matches_the_registry():
    listed = get_documentation_resource_list()
    assert len(listed) == len(DOCUMENTATION_RESOURCES)
    assert {r["uri"] for r in listed} == set(DOCUMENTATION_RESOURCES)
    assert all(r["mimeType"] == "text/markdown" for r in listed)


# ── markdown preference ──────────────────────────────────────────────────────


def test_markdown_url_appends_suffix_but_never_doubles_it():
    assert _markdown_url("https://docs.litmus.io/uns") == "https://docs.litmus.io/uns.md"
    assert _markdown_url("https://api.litmus.io/agents.md") == (
        "https://api.litmus.io/agents.md"
    )
    # llms.txt already names a file; appending .md would 404 it.
    assert _markdown_url("https://api.litmus.io/llms.txt") == (
        "https://api.litmus.io/llms.txt"
    )


def test_text_plain_is_accepted_for_a_named_text_file():
    """llms.txt is served as text/plain, and is fetched in one request."""
    seen = []

    async def handler(request):
        seen.append(str(request.url))
        return _fake_response(content_type="text/plain", text="# Litmus API\n" + "z" * 300)

    with _mocked_http(handler):
        body = run(fetch_documentation_content("https://api.litmus.io/llms.txt"))

    assert seen == ["https://api.litmus.io/llms.txt"], "no .md probe, no HTML fallback"
    assert body.startswith("# Litmus API")


def test_text_plain_is_still_rejected_for_a_docs_page():
    """Only a named text file gets the text/plain allowance.

    A docs page whose twin answers as text/plain is most likely the app shell,
    so it must still fall back to the HTML URL.
    """

    async def handler(request):
        if str(request.url).endswith(".md"):
            return _fake_response(content_type="text/plain", text=SPA_SHELL)
        return _fake_response(
            content_type="text/html", text="<html><main>real page</main></html>"
        )

    with _mocked_http(handler):
        body = run(fetch_documentation_content("https://docs.litmus.io/x"))

    assert "real page" in body


def test_strip_frontmatter_removes_only_the_leading_block():
    stripped = _strip_frontmatter("---\ntitle: T\n---\n\nbody --- still body")
    assert stripped == "body --- still body"
    assert _strip_frontmatter("no frontmatter") == "no frontmatter"


def test_markdown_is_preferred_and_frontmatter_dropped():
    async def handler(request):
        assert str(request.url).endswith(".md")
        return _fake_response(text=MARKDOWN_PAGE)

    with _mocked_http(handler):
        body = run(fetch_documentation_content("https://docs.litmus.io/x"))

    assert body.startswith("DeviceHub connects")
    assert "title: DeviceHub" not in body


def test_html_shell_served_with_status_200_is_rejected():
    """Archbee answers unknown .md paths with 200 and the SPA shell.

    Trusting the status code would serve ~20 KB of navigation markup as
    documentation the first time a page is renamed upstream.
    """
    seen = []

    async def handler(request):
        url = str(request.url)
        seen.append(url)
        if url.endswith(".md"):
            return _fake_response(content_type="text/html", text=SPA_SHELL)
        return _fake_response(
            content_type="text/html", text="<html><main>real page</main></html>"
        )

    with _mocked_http(handler):
        body = run(fetch_documentation_content("https://docs.litmus.io/x"))

    assert len(seen) == 2, "should fall back to the HTML URL"
    assert "real page" in body
    assert "DOCTYPE" not in body


def test_frontmatter_only_page_falls_back_to_html():
    """A docs page with no body of its own yields frontmatter and nothing else."""

    async def handler(request):
        if str(request.url).endswith(".md"):
            return _fake_response(text="---\ntitle: Landing\nslug: x\n---\n\n")
        return _fake_response(
            content_type="text/html", text="<html><main>html body</main></html>"
        )

    with _mocked_http(handler):
        body = run(fetch_documentation_content("https://docs.litmus.io/x"))

    assert "html body" in body


def test_non_200_markdown_falls_back_to_html():
    async def handler(request):
        if str(request.url).endswith(".md"):
            return _fake_response(status=404, content_type="text/html", text="nope")
        return _fake_response(
            content_type="text/html", text="<html><main>fallback</main></html>"
        )

    with _mocked_http(handler):
        assert "fallback" in run(fetch_documentation_content("https://docs.litmus.io/x"))


# ── the generated overview ───────────────────────────────────────────────────


def test_overview_is_generated_locally_and_indexes_the_others():
    """The docs landing page has no body upstream, so this one is built here.

    It must not perform a fetch, and it must name the other resources so it
    cannot silently drift from what the server advertises.
    """
    fetcher = AsyncMock(return_value="should not be called")
    with patch.object(resource_tools, "fetch_documentation_content", new=fetcher):
        text = run(read_documentation_resource("litmus://docs/overview"))[0].text

    fetcher.assert_not_awaited()
    indexed = [u for u, i in DOCUMENTATION_RESOURCES.items() if not i.get("index")]
    assert indexed, "nothing to index"
    for uri in indexed:
        assert uri in text
