"""Per-call LEM bridge routing: schema injection, header overlay, and the
LEM-only guidance error from get_litmus_connection."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from starlette.requests import Request

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp.shared.exceptions import McpError  # noqa: E402

import server  # noqa: E402
from server import (  # noqa: E402
    ALL_TOOLS,
    BridgeOverlayRequest,
    _is_bridgeable,
    _with_bridge_args,
)
from utils.auth import get_litmus_connection  # noqa: E402
from tools.sdk_cli_tools import _build_cli_env  # noqa: E402


def _make_request(headers):
    request = Mock(spec=Request)
    request.headers = headers
    return request


class _DictHeaders:
    """dict-backed headers with .get, mirroring HeaderDict."""

    def __init__(self, headers):
        self._headers = {k.lower(): v for k, v in headers.items()}

    def get(self, key, default=None):
        return self._headers.get(key.lower(), default)


LEM_ONLY_HEADERS = {
    "EDGE_MANAGER_URL": "https://lem.example.com",
    "EDGE_API_TOKEN": "lem-token",
}


# ── category classification ─────────────────────────────────────────────────


def test_le_tools_are_bridgeable_and_lem_tools_are_not():
    by_name = {t["name"]: t for t in ALL_TOOLS}
    assert _is_bridgeable(by_name["get_devicehub_devices"])
    assert _is_bridgeable(by_name["list_static_attributes"])
    assert _is_bridgeable(by_name["litmus_sdk_read"])
    assert not _is_bridgeable(by_name["lem_list_devices"])
    assert not _is_bridgeable(by_name["get_current_value_from_topic"])


# ── schema injection ─────────────────────────────────────────────────────────


def test_with_bridge_args_adds_optional_ids_without_mutating_original():
    original = {"type": "object", "properties": {}, "required": []}
    injected = _with_bridge_args(original)
    assert "project_id" in injected["properties"]
    assert "device_id" in injected["properties"]
    assert "project_id" not in original["properties"]
    # bridge ids are never required
    assert "project_id" not in injected.get("required", [])


def test_with_bridge_args_does_not_clobber_existing_property():
    original = {
        "type": "object",
        "properties": {"project_id": {"type": "string", "description": "mine"}},
    }
    injected = _with_bridge_args(original)
    assert injected["properties"]["project_id"]["description"] == "mine"


# ── header overlay ───────────────────────────────────────────────────────────


def test_overlay_injects_bridge_ids_and_passes_through_other_headers():
    base = _make_request(
        _DictHeaders({**LEM_ONLY_HEADERS, "VALIDATE_CERTIFICATE": "true"})
    )
    overlaid = BridgeOverlayRequest(base, "proj-1", "dev-1")
    assert overlaid.headers.get("EDGE_MANAGER_PROJECT_ID") == "proj-1"
    assert overlaid.headers.get("EDGE_MANAGER_DEVICE_ID") == "dev-1"
    assert overlaid.headers.get("EDGE_MANAGER_URL") == "https://lem.example.com"
    assert overlaid.headers.get("VALIDATE_CERTIFICATE") == "true"


def test_overlay_wins_over_configured_header():
    base = _make_request(
        _DictHeaders({**LEM_ONLY_HEADERS, "EDGE_MANAGER_DEVICE_ID": "static-dev"})
    )
    overlaid = BridgeOverlayRequest(base, "proj-1", "dev-override")
    assert overlaid.headers.get("EDGE_MANAGER_DEVICE_ID") == "dev-override"


def test_overlay_partial_falls_back_to_configured_header():
    base = _make_request(
        _DictHeaders({**LEM_ONLY_HEADERS, "EDGE_MANAGER_PROJECT_ID": "proj-static"})
    )
    overlaid = BridgeOverlayRequest(base, "", "dev-1")
    assert overlaid.headers.get("EDGE_MANAGER_PROJECT_ID") == "proj-static"
    assert overlaid.headers.get("EDGE_MANAGER_DEVICE_ID") == "dev-1"


def test_cli_env_enables_bridge_through_overlay():
    base = _make_request(_DictHeaders(LEM_ONLY_HEADERS))
    overlaid = BridgeOverlayRequest(base, "proj-1", "dev-1")
    env = _build_cli_env(overlaid)
    assert env["USE_LEM_BRIDGE"] == "true"
    assert env["EDGE_MANAGER_PROJECT_ID"] == "proj-1"
    assert env["EDGE_MANAGER_DEVICE_ID"] == "dev-1"


# ── get_litmus_connection guidance ───────────────────────────────────────────


def test_lem_only_config_yields_bridge_guidance_error():
    request = _make_request(_DictHeaders(LEM_ONLY_HEADERS))
    with pytest.raises(McpError) as exc_info:
        get_litmus_connection(request)
    message = str(exc_info.value)
    assert "project_id" in message
    assert "device_id" in message
    assert "lem_list_devices" in message


def test_missing_everything_still_reports_edge_url_required():
    request = _make_request(_DictHeaders({}))
    with pytest.raises(McpError) as exc_info:
        get_litmus_connection(request)
    assert "EDGE_URL" in str(exc_info.value)


# ── call-time argument stripping ─────────────────────────────────────────────


def test_handle_call_tool_strips_bridge_args_and_overlays_request():
    """project_id/device_id are consumed by the dispatcher, not passed to the
    tool handler, and the handler sees an overlaid request."""
    import asyncio

    seen = {}

    async def fake_handler(request, arguments):
        seen["arguments"] = arguments
        seen["project"] = request.headers.get("EDGE_MANAGER_PROJECT_ID")
        seen["device"] = request.headers.get("EDGE_MANAGER_DEVICE_ID")
        from mcp.types import TextContent

        return [TextContent(type="text", text="{}")]

    tool_name = "get_devicehub_devices"
    tool = server.TOOL_BY_NAME[tool_name]
    original_handler = tool["handler"]
    base = _make_request(_DictHeaders(LEM_ONLY_HEADERS))
    token = server.current_request.set(base)
    try:
        tool["handler"] = fake_handler
        asyncio.run(
            server.handle_call_tool(
                tool_name,
                {"filter_by_driver": "ModbusTCP", "project_id": "p1", "device_id": "d1"},
            )
        )
    finally:
        tool["handler"] = original_handler
        server.current_request.reset(token)

    assert seen["arguments"] == {"filter_by_driver": "ModbusTCP"}
    assert seen["project"] == "p1"
    assert seen["device"] == "d1"
