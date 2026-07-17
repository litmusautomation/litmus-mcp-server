"""CLI-backed curated tools: digital twin attribute listing and tag status.

run_cli_function is mocked throughout; these tests pin which litmus-cli
functions are invoked, argument shapes, fan-out behavior, and error surfacing.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from starlette.requests import Request

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp.shared.exceptions import McpError  # noqa: E402

import tools.digitaltwins_tools as dt_tools  # noqa: E402
import tools.devicehub_tools as dh_tools  # noqa: E402
from tools.digitaltwins_tools import (  # noqa: E402
    list_static_attributes_tool,
    list_dynamic_attributes_tool,
    list_transformations_tool,
)
from tools.devicehub_tools import get_tag_status, get_all_tags_status  # noqa: E402
from tools.sdk_cli_tools import CLIFunctionError  # noqa: E402


def _make_request(headers=None):
    request = Mock(spec=Request)
    request.headers = headers or {
        "EDGE_URL": "https://test-edge.local:8443",
        "EDGE_API_CLIENT_ID": "test-client-id",
        "EDGE_API_CLIENT_SECRET": "test-secret",
    }
    return request


def _run(coro):
    return asyncio.run(coro)


def _parse(result):
    return json.loads(result[0].text)


INSTANCES = [
    {"ID": "inst-1", "ModelID": "model-1", "Name": "Pump-A"},
    {"ID": "inst-2", "ModelID": "model-1", "Name": "Pump-B"},
]


# ── digital twin attributes ──────────────────────────────────────────────────


def test_static_attributes_by_instance_id_calls_cli():
    mock = AsyncMock(return_value=[{"ID": "a1", "Key": "serial", "Value": "42"}])
    with patch.object(dt_tools, "run_cli_function", mock):
        data = _parse(
            _run(list_static_attributes_tool(_make_request(), {"instance_id": "inst-2"}))
        )
    assert data["success"] is True
    assert data["count"] == 1
    assert data["instance_id"] == "inst-2"
    mock.assert_awaited_once()
    assert mock.await_args.args[1] == "le.digitaltwins.ListStaticAttributes"
    assert mock.await_args.args[2] == {"instanceID": "inst-2"}


def test_static_attributes_by_model_id_uses_model_arg():
    mock = AsyncMock(return_value=[])
    with patch.object(dt_tools, "run_cli_function", mock):
        data = _parse(
            _run(list_static_attributes_tool(_make_request(), {"model_id": "model-1"}))
        )
    assert data["success"] is True
    assert mock.await_args.args[1] == "le.digitaltwins.ListStaticAttributes"
    assert mock.await_args.args[2] == {"modelID": "model-1"}


def test_attributes_instance_name_is_resolved_to_id():
    async def fake_cli(request, function, args):
        if function == "le.digitaltwins.ListAllInstances":
            return INSTANCES
        assert function == "le.digitaltwins.ListDynamicAttributes"
        assert args == {"instanceID": "inst-2"}
        return [{"ID": "d1", "Name": "temperature"}]

    with patch.object(dt_tools, "run_cli_function", side_effect=fake_cli):
        data = _parse(
            _run(
                list_dynamic_attributes_tool(
                    _make_request(), {"instance_name": "pump-b"}
                )
            )
        )
    assert data["success"] is True
    assert data["instance_id"] == "inst-2"
    assert data["count"] == 1


def test_attributes_unknown_instance_name_lists_available():
    async def fake_cli(request, function, args):
        assert function == "le.digitaltwins.ListAllInstances"
        return INSTANCES

    with patch.object(dt_tools, "run_cli_function", side_effect=fake_cli):
        with pytest.raises(McpError) as exc_info:
            _run(
                list_static_attributes_tool(
                    _make_request(), {"instance_name": "Ghost"}
                )
            )
    message = str(exc_info.value)
    assert "Ghost" in message and "Pump-A" in message


def test_attributes_all_instances_fans_out_over_every_twin():
    calls = []

    async def fake_cli(request, function, args):
        calls.append((function, args))
        if function == "le.digitaltwins.ListAllInstances":
            return INSTANCES
        return [{"ID": f"attr-{args['instanceID']}", "Key": "k", "Value": "v"}]

    with patch.object(dt_tools, "run_cli_function", side_effect=fake_cli):
        data = _parse(
            _run(
                list_static_attributes_tool(_make_request(), {"all_instances": True})
            )
        )

    assert data["success"] is True
    assert data["instance_count"] == 2
    assert data["count"] == 2
    queried = {
        args["instanceID"]
        for function, args in calls
        if function == "le.digitaltwins.ListStaticAttributes"
    }
    assert queried == {"inst-1", "inst-2"}
    names = {e["instance_name"] for e in data["instances"]}
    assert names == {"Pump-A", "Pump-B"}


def test_attributes_all_instances_reports_per_instance_error():
    async def fake_cli(request, function, args):
        if function == "le.digitaltwins.ListAllInstances":
            return INSTANCES
        if args["instanceID"] == "inst-1":
            raise CLIFunctionError(function, "edge exploded")
        return []

    with patch.object(dt_tools, "run_cli_function", side_effect=fake_cli):
        data = _parse(
            _run(
                list_dynamic_attributes_tool(_make_request(), {"all_instances": True})
            )
        )
    assert data["success"] is True
    by_id = {e["instance_id"]: e for e in data["instances"]}
    assert "error" in by_id["inst-1"]
    assert by_id["inst-2"]["count"] == 0


def test_attributes_require_exactly_one_selector():
    for bad_args in (
        {},
        {"model_id": "m", "instance_id": "i"},
        {"instance_id": "i", "all_instances": True},
    ):
        with pytest.raises(McpError):
            _run(list_static_attributes_tool(_make_request(), bad_args))


def test_attributes_cli_null_result_treated_as_empty():
    mock = AsyncMock(return_value=None)
    with patch.object(dt_tools, "run_cli_function", mock):
        data = _parse(
            _run(list_static_attributes_tool(_make_request(), {"model_id": "m1"}))
        )
    assert data["success"] is True
    assert data["count"] == 0


def test_transformations_via_cli():
    mock = AsyncMock(return_value=[{"ID": "t1"}])
    with patch.object(dt_tools, "run_cli_function", mock):
        data = _parse(
            _run(list_transformations_tool(_make_request(), {"model_id": "model-1"}))
        )
    assert data["success"] is True
    assert data["count"] == 1
    assert mock.await_args.args[1] == "le.digitaltwins.ListTransformations"
    assert mock.await_args.args[2] == {"modelID": "model-1"}


def test_attributes_cli_failure_returns_error_response():
    mock = AsyncMock(side_effect=CLIFunctionError("f", "binary blew up"))
    with patch.object(dt_tools, "run_cli_function", mock):
        data = _parse(
            _run(list_static_attributes_tool(_make_request(), {"model_id": "m1"}))
        )
    assert data["success"] is False
    assert "binary blew up" in data["message"]


# ── tag status ───────────────────────────────────────────────────────────────

DEVICE_TAGS = [
    {"ID": "tag-1", "TagName": "Temperature"},
    {"ID": "tag-2", "TagName": "Pressure"},
]
TAG_STATES = [
    {"ID": "tag-1", "State": "OK"},
    {"ID": "tag-2", "State": "ERROR"},
]


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools._find_device_by_name")
def test_get_tag_status_via_cli(mock_find, mock_conn):
    mock_conn.return_value = MagicMock()
    device = MagicMock()
    device.id = "dev-1"
    mock_find.return_value = device

    async def fake_cli(request, function, args):
        if function == "le.devicehub.ListDeviceTags":
            assert args["deviceID"] == "dev-1"
            return DEVICE_TAGS
        assert function == "le.devicehub.TagStatus"
        assert set(args["tagIDs"]) == {"tag-1", "tag-2"}
        return TAG_STATES

    with patch.object(dh_tools, "run_cli_function", side_effect=fake_cli):
        data = _parse(
            _run(get_tag_status(_make_request(), {"device_name": "TestDevice"}))
        )

    assert data["success"] is True
    assert data["count"] == 2
    by_tag = {s["tag_name"]: s["State"] for s in data["statuses"]}
    assert by_tag == {"Temperature": "OK", "Pressure": "ERROR"}


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools._find_device_by_name")
def test_get_tag_status_single_tag_filter(mock_find, mock_conn):
    mock_conn.return_value = MagicMock()
    device = MagicMock()
    device.id = "dev-1"
    mock_find.return_value = device

    async def fake_cli(request, function, args):
        return DEVICE_TAGS if function == "le.devicehub.ListDeviceTags" else TAG_STATES

    with patch.object(dh_tools, "run_cli_function", side_effect=fake_cli):
        data = _parse(
            _run(
                get_tag_status(
                    _make_request(),
                    {"device_name": "TestDevice", "tag_name": "Pressure"},
                )
            )
        )
    assert data["count"] == 1
    assert data["statuses"][0]["State"] == "ERROR"


def test_get_all_tags_status_covers_every_device_and_surfaces_errors():
    devices = [
        {"ID": "dev-1", "Name": "Alpha"},
        {"ID": "dev-2", "Name": "Beta"},
        {"ID": "dev-3", "Name": "Gamma"},
    ]

    async def fake_cli(request, function, args):
        if function == "le.devicehub.ListDevices":
            return devices
        device_id = args["deviceID"]
        if device_id == "dev-3":
            raise CLIFunctionError(function, "unreachable")
        if function == "le.devicehub.ListDeviceTags":
            return [{"ID": f"{device_id}-t", "TagName": f"{device_id}-tag"}]
        return [{"ID": f"{device_id}-t", "State": "OK" if device_id == "dev-1" else "ERROR"}]

    with patch.object(dh_tools, "run_cli_function", side_effect=fake_cli):
        data = _parse(_run(get_all_tags_status(_make_request(), {})))

    # default filter: only non-OK statuses, but both healthy devices were checked
    assert data["success"] is True
    assert data["devices_checked"] == 3
    assert [s["device_name"] for s in data["statuses"]] == ["Beta"]
    assert data["device_errors"] == [
        {"device_name": "Gamma", "error": "unreachable"}
    ]


def test_get_all_tags_status_filter_all_returns_everything():
    async def fake_cli(request, function, args):
        if function == "le.devicehub.ListDevices":
            return [{"ID": "dev-1", "Name": "Alpha"}]
        if function == "le.devicehub.ListDeviceTags":
            return DEVICE_TAGS
        return TAG_STATES

    with patch.object(dh_tools, "run_cli_function", side_effect=fake_cli):
        data = _parse(
            _run(get_all_tags_status(_make_request(), {"filter_status": ""}))
        )
    assert data["count"] == 2
