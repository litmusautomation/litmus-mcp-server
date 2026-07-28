"""Tests that tool schemas reject arguments they do not declare.

The evaluation's finding was that a misspelled argument was accepted silently
and the tool ran with defaults: `get_multiple_values_from_topic` asked for 2
samples over 20s ran 10 over 30s and said so, with nothing contradicting the
caller. The SDK validates input against the advertised schema, so declaring
`additionalProperties: false` turns that into an error naming the bad argument.

These tests drive the real list_tools and call_tool handlers, because what a
call is validated against is what list_tools last advertised, not what the
TOOLS registry holds.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import Mock

from mcp import types
from starlette.requests import Request

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import server  # noqa: E402
from server import ALL_TOOLS, _is_bridgeable, _strict  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class _DictHeaders:
    def __init__(self, headers):
        self._headers = {k.lower(): v for k, v in headers.items()}

    def get(self, key, default=None):
        return self._headers.get(key.lower(), default)


def _request(headers):
    request = Mock(spec=Request)
    request.headers = _DictHeaders(headers)
    return request


EDGE = {
    "EDGE_URL": "https://10.0.0.5",
    "EDGE_API_CLIENT_ID": "id",
    "EDGE_API_CLIENT_SECRET": "secret",
}
LEM = {"EDGE_MANAGER_URL": "https://lem.example.com", "EDGE_API_TOKEN": "token"}


def _list_tools(headers):
    token = server.current_request.set(_request(headers))
    try:
        return run(server.handle_list_tools())
    finally:
        server.current_request.reset(token)


def _call(name, arguments, headers):
    """Drive the SDK's CallToolRequest handler so input validation runs.

    The tool's own handler is stubbed out: validation happens in the SDK layer
    before the handler is reached, so stubbing keeps these tests offline
    instead of waiting on a broker that is not there.
    """
    handler = server.mcp.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )

    async def stub(_request, _arguments):
        return [types.TextContent(type="text", text='{"stubbed": true}')]

    tool = server.TOOL_BY_NAME[name]
    original = tool["handler"]
    token = server.current_request.set(_request(headers))
    try:
        tool["handler"] = stub
        return run(handler(request))
    finally:
        tool["handler"] = original
        server.current_request.reset(token)


# ── the invariant ────────────────────────────────────────────────────────────


def test_every_advertised_tool_rejects_unknown_arguments():
    tools = _list_tools(EDGE)
    assert len(tools) == len(ALL_TOOLS)
    missing = [
        t.name
        for t in tools
        if t.inputSchema.get("additionalProperties") is not False
    ]
    assert not missing, f"tools still accepting unknown arguments: {missing}"


def test_strict_does_not_mutate_the_registry_schema():
    original = {"type": "object", "properties": {"a": {"type": "string"}}}
    strict = _strict(original)
    assert strict["additionalProperties"] is False
    assert "additionalProperties" not in original


def test_strict_supplies_object_type_when_absent():
    assert _strict({})["type"] == "object"


# ── the behaviour the finding described ──────────────────────────────────────


def test_misspelled_argument_is_refused_by_name():
    """The reviewer's exact repro: count/timeout instead of num_samples."""
    result = _call(
        "get_multiple_values_from_topic",
        {"topic": "devicehub.alias.M1.PartMade", "count": 2, "timeout": 20},
        EDGE,
    )
    payload = result.root
    assert payload.isError is True
    text = payload.content[0].text
    assert "validation error" in text.lower()
    # The message has to name the offending argument to be actionable.
    assert "count" in text or "timeout" in text


def test_correctly_spelled_arguments_still_validate():
    """The guard must not reject a well-formed call."""
    result = _call(
        "get_multiple_values_from_topic",
        {"topic": "x", "num_samples": 2},
        EDGE,
    )
    assert result.root.isError is not True
    assert "stubbed" in result.root.content[0].text


# ── the shared tool cache ────────────────────────────────────────────────────


def test_bridge_args_are_advertised_without_lem_credentials():
    """Advertised unconditionally, so a shared cache cannot strip them.

    The SDK keeps one process-wide tool cache that every list_tools call
    clears and refreshes, and call_tool validates against it. If the bridge
    arguments were only advertised to LEM-configured clients, a non-LEM
    client's listing would make a LEM client's bridged call fail validation.
    """
    for headers in (EDGE, LEM):
        by_name = {t.name: t for t in _list_tools(headers)}
        props = by_name["get_devicehub_devices"].inputSchema["properties"]
        assert "project_id" in props
        assert "lem_device_id" in props


def test_bridged_call_survives_a_non_lem_client_listing_first():
    """The regression the unconditional advertisement exists to prevent."""
    _list_tools(EDGE)  # non-LEM client refreshes the shared cache
    result = _call(
        "get_devicehub_devices",
        {"project_id": "p1", "lem_device_id": "d1"},
        LEM,
    )
    text = result.root.content[0].text
    assert "additional properties" not in text.lower()
    assert "validation error" not in text.lower()


def test_deprecated_device_id_still_passes_validation():
    """The alias is declared, so strict validation does not pre-empt it."""
    _list_tools(EDGE)
    result = _call(
        "get_devicehub_devices", {"project_id": "p1", "device_id": "d1"}, LEM
    )
    text = result.root.content[0].text
    assert "validation error" not in text.lower()


def test_non_bridgeable_tools_do_not_advertise_bridge_args():
    by_name = {t.name: t for t in _list_tools(LEM)}
    lem_tool = next(t for t in ALL_TOOLS if not _is_bridgeable(t))
    props = by_name[lem_tool["name"]].inputSchema.get("properties", {})
    assert "lem_device_id" not in props
