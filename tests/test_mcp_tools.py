"""
Tests for Litmus MCP Server Tools

Covers the tool handler functions in tools/*.py.
All tests mock the Litmus SDK connection and SDK calls so no real
Edge instance is needed.

Response shape: list[TextContent] where result[0].text is JSON.
Parse with json.loads(result[0].text) and check "success" key.
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, MagicMock, patch
from starlette.requests import Request

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp.shared.exceptions import McpError
from tools.devicehub_tools import (
    get_litmusedge_driver_list,
    get_devicehub_devices,
    create_devicehub_device,
    get_devicehub_device_tags,
    get_current_value_of_devicehub_tag,
)
from tools.dm_tools import (
    get_litmusedge_friendly_name,
    set_litmusedge_friendly_name,
)
from tools.marketplace_tools import (
    get_all_containers_on_litmusedge,
    run_docker_container_on_litmusedge,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_request(headers=None):
    request = Mock(spec=Request)
    request.headers = headers or {
        "EDGE_URL": "https://test-edge.local:8443",
        "EDGE_API_CLIENT_ID": "test-client-id",
        "EDGE_API_CLIENT_SECRET": "test-secret",
        "VALIDATE_CERTIFICATE": "false",
    }
    return request


def _run(coro):
    return asyncio.run(coro)


def _parse(result):
    return json.loads(result[0].text)


# ── get_litmusedge_driver_list ───────────────────────────────────────────────


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.list_all_drivers")
def test_driver_list_success(mock_list_drivers, mock_connection):
    """Returns success with sorted driver list."""
    mock_connection.return_value = MagicMock()
    d1, d2 = MagicMock(), MagicMock()
    d1.name = "OPCUA"
    d2.name = "ModbusTCP"
    for d in (d1, d2):
        d.id = d.protocol = d.version = d.description = d.category = None
    mock_list_drivers.return_value = [d1, d2]

    result = _run(get_litmusedge_driver_list(_make_request()))
    data = _parse(result)

    assert data["success"] is True
    assert data["count"] == 2
    # sorted alphabetically
    assert data["driver_names"] == ["ModbusTCP", "OPCUA"]


@patch("tools.devicehub_tools.get_litmus_connection")
def test_driver_list_auth_failure(mock_connection):
    """Missing auth headers raises McpError before SDK call."""
    mock_connection.side_effect = McpError(
        type("E", (), {"code": -32602, "message": "EDGE_URL header is required"})()
    )

    with pytest.raises(McpError):
        _run(get_litmusedge_driver_list(_make_request(headers={})))


# ── get_devicehub_devices ───────────────────────────────────────────────────


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.devices.list_devices")
def test_get_devices_success(mock_list_devices, mock_connection):
    """Returns success with device list."""
    mock_connection.return_value = MagicMock()
    dev = MagicMock()
    dev.name = "TestDevice"
    dev.id = "d-1"
    dev.driver = "ModbusTCP"
    dev.metadata = dev.description = dev.properties = None
    mock_list_devices.return_value = [dev]

    result = _run(get_devicehub_devices(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert data["count"] == 1
    assert data["devices"][0]["name"] == "TestDevice"


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.devices.list_devices")
def test_get_devices_filter_by_driver(mock_list_devices, mock_connection):
    """filter_by_driver excludes non-matching devices."""
    mock_connection.return_value = MagicMock()
    dev1, dev2 = MagicMock(), MagicMock()
    dev1.name = "ModbusDevice"
    dev1.driver = "ModbusTCP"
    dev1.id = dev1.metadata = dev1.description = dev1.properties = None
    dev2.name = "OPCDevice"
    dev2.driver = "OPCUA"
    dev2.id = dev2.metadata = dev2.description = dev2.properties = None
    mock_list_devices.return_value = [dev1, dev2]

    result = _run(get_devicehub_devices(_make_request(), {"filter_by_driver": "ModbusTCP"}))
    data = _parse(result)

    assert data["count"] == 1
    assert data["devices"][0]["name"] == "ModbusDevice"


# ── create_devicehub_device ─────────────────────────────────────────────────


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.list_all_drivers")
@patch("tools.devicehub_tools.devices.create_device")
@patch("tools.devicehub_tools.devices.Device")
def test_create_device_success(mock_Device, mock_create, mock_list_drivers, mock_connection):
    """Creates device and returns success."""
    mock_connection.return_value = MagicMock()
    driver = MagicMock()
    driver.name = "ModbusTCP"
    driver.id = "drv-1"
    driver.get_default_properties.return_value = {"ip": "10.0.0.1"}
    mock_list_drivers.return_value = [driver]
    # Use a simple namespace so __dict__ works correctly
    created = type("Device", (), {"id": "dev-1", "name": "NewDevice"})()
    mock_create.return_value = created

    args = {"name": "NewDevice", "selected_driver": "ModbusTCP"}
    result = _run(create_devicehub_device(_make_request(), args))
    data = _parse(result)

    assert data["success"] is True
    assert "next_steps" in data


def test_create_device_missing_name():
    """Missing 'name' raises McpError."""
    with patch("tools.devicehub_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(create_devicehub_device(_make_request(), {"selected_driver": "ModbusTCP"}))


def test_create_device_missing_driver():
    """Missing 'selected_driver' raises McpError."""
    with patch("tools.devicehub_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(create_devicehub_device(_make_request(), {"name": "Dev"}))


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.list_all_drivers")
def test_create_device_invalid_driver(mock_list_drivers, mock_connection):
    """Unknown driver raises McpError with available drivers listed."""
    mock_connection.return_value = MagicMock()
    driver = MagicMock()
    driver.name = "ModbusTCP"
    driver.id = "drv-1"
    driver.get_default_properties.return_value = {}
    mock_list_drivers.return_value = [driver]

    with pytest.raises(McpError) as exc_info:
        _run(create_devicehub_device(_make_request(), {"name": "Dev", "selected_driver": "BadDriver"}))

    assert "not found" in str(exc_info.value).lower()


# ── get_devicehub_device_tags ───────────────────────────────────────────────


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.devices.list_devices")
@patch("tools.devicehub_tools.api.gql_query")
def test_get_device_tags_success(mock_gql_query, mock_list_devices, mock_connection):
    """Returns tags for a known device."""
    mock_connection.return_value = MagicMock()
    dev = MagicMock()
    dev.name = "TestDevice"
    dev.id = "dev-1"
    mock_list_devices.return_value = [dev]
    # First call: count query; second call: list query
    mock_gql_query.side_effect = [
        {"data": {"ListRegisters": {"TotalCount": 1}}},
        {"data": {"ListRegisters": {"Registers": [{"TagName": "Temperature"}]}}},
    ]

    result = _run(get_devicehub_device_tags(_make_request(), {"device_name": "TestDevice"}))
    data = _parse(result)

    assert data["success"] is True
    assert data["tag_names"] == ["Temperature"]


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.api.gql_query")
def test_get_device_tags_missing_device_name(mock_gql_query, mock_connection):
    """No 'device_name' queries all devices (all-devices path, no McpError)."""
    mock_connection.return_value = MagicMock()
    mock_gql_query.side_effect = [
        {"data": {"ListRegistersFromAllDevices": {"TotalCount": 1}}},
        {"data": {"ListRegistersFromAllDevices": {"Registers": [{"TagName": "Pressure"}]}}},
    ]

    result = _run(get_devicehub_device_tags(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert data["tag_names"] == ["Pressure"]


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.devices.list_devices")
def test_get_device_tags_device_not_found(mock_list_devices, mock_connection):
    """Unknown device raises McpError."""
    mock_connection.return_value = MagicMock()
    mock_list_devices.return_value = []

    with pytest.raises(McpError) as exc_info:
        _run(get_devicehub_device_tags(_make_request(), {"device_name": "Ghost"}))

    assert "not found" in str(exc_info.value).lower()


# ── get_current_value_of_devicehub_tag ─────────────────────────────────────


def test_get_tag_value_missing_device_name():
    """Missing 'device_name' raises McpError."""
    with patch("tools.devicehub_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(get_current_value_of_devicehub_tag(_make_request(), {"tag_name": "Temp"}))


def test_get_tag_value_missing_both_identifiers():
    """Missing both tag_name and tag_id raises McpError."""
    with patch("tools.devicehub_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(get_current_value_of_devicehub_tag(_make_request(), {"device_name": "Dev"}))


# ── get_litmusedge_friendly_name ────────────────────────────────────────────


@patch("tools.dm_tools.get_litmus_connection")
@patch("tools.dm_tools.network.get_friendly_name")
def test_get_friendly_name_success(mock_get_name, mock_connection):
    """Returns the friendly name."""
    mock_connection.return_value = MagicMock()
    mock_get_name.return_value = "Factory_Gateway"

    result = _run(get_litmusedge_friendly_name(_make_request()))
    data = _parse(result)

    assert data["success"] is True
    assert data["friendly_name"] == "Factory_Gateway"


# ── set_litmusedge_friendly_name ────────────────────────────────────────────


@patch("tools.dm_tools.get_litmus_connection")
@patch("tools.dm_tools.network.set_friendly_name")
def test_set_friendly_name_success(mock_set_name, mock_connection):
    """Sets the friendly name and returns confirmation."""
    mock_connection.return_value = MagicMock()
    mock_set_name.return_value = None

    result = _run(set_litmusedge_friendly_name(_make_request(), {"new_friendly_name": "NewName"}))
    data = _parse(result)

    assert data["success"] is True
    assert data["friendly_name"] == "NewName"


def test_set_friendly_name_missing_param():
    """Missing 'new_friendly_name' raises McpError."""
    with patch("tools.dm_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(set_litmusedge_friendly_name(_make_request(), {}))


# ── get_all_containers_on_litmusedge ────────────────────────────────────────


@patch("tools.marketplace_tools.get_litmus_connection")
@patch("tools.marketplace_tools.list_all_containers")
def test_get_containers_success(mock_list_containers, mock_connection):
    """Returns container list with count."""
    mock_connection.return_value = MagicMock()
    mock_list_containers.return_value = [
        {"name": "node-red", "status": "running"},
        {"name": "influxdb", "status": "running"},
    ]

    result = _run(get_all_containers_on_litmusedge(_make_request()))
    data = _parse(result)

    assert data["success"] is True
    assert data["count"] == 2
    assert data["containers"][0]["name"] == "node-red"


# ── run_docker_container_on_litmusedge ──────────────────────────────────────


@patch("tools.marketplace_tools.get_litmus_connection")
@patch("tools.marketplace_tools.run_container")
def test_run_container_success(mock_run_container, mock_connection):
    """Runs container and returns container_id."""
    mock_connection.return_value = MagicMock()
    mock_run_container.return_value = {"id": "abc123"}

    args = {"docker_run_command": "docker run -d nginx:latest"}
    result = _run(run_docker_container_on_litmusedge(_make_request(), args))
    data = _parse(result)

    assert data["success"] is True
    assert data["container_id"] == "abc123"


def test_run_container_missing_command():
    """Missing 'docker_run_command' raises McpError."""
    with patch("tools.marketplace_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(run_docker_container_on_litmusedge(_make_request(), {}))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
