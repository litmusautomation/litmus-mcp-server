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
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from starlette.requests import Request

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp.shared.exceptions import McpError
import tools.devicehub_tools as devicehub_tools
from tools.devicehub_tools import (
    get_litmusedge_driver_list,
    get_devicehub_devices,
    create_devicehub_device,
    get_devicehub_device_tags,
    get_current_value_of_devicehub_tag,
    get_device_connection_status,
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

    result = _run(
        get_devicehub_devices(_make_request(), {"filter_by_driver": "ModbusTCP"})
    )
    data = _parse(result)

    assert data["count"] == 1
    assert data["devices"][0]["name"] == "ModbusDevice"


# ── create_devicehub_device ─────────────────────────────────────────────────


def test_create_device_success():
    """Creates a device through litmus-cli and returns its new ID."""
    driver = {"ID": "drv-1", "Name": "ModbusTCP", "Properties": []}

    async def fake_cli(request, function, args):
        if function == "le.devicehub.ListDrivers":
            return [driver]
        assert function == "le.devicehub.CreateDefaultDevice"
        # The driver JSON from ListDrivers must be passed through verbatim
        assert args == {"driver": driver, "name": "NewDevice", "params": {}}
        return {"ID": "dev-1", "Name": "NewDevice", "Description": ""}

    with patch.object(devicehub_tools, "run_cli_function", side_effect=fake_cli):
        result = _run(
            create_devicehub_device(
                _make_request(),
                {"name": "NewDevice", "selected_driver": "ModbusTCP"},
            )
        )
    data = _parse(result)

    assert data["success"] is True
    assert data["device"]["id"] == "dev-1"
    assert data["device"]["driver"] == "ModbusTCP"
    assert "next_steps" in data


def test_create_device_missing_name():
    """Missing 'name' raises McpError."""
    with patch("tools.devicehub_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(
                create_devicehub_device(
                    _make_request(), {"selected_driver": "ModbusTCP"}
                )
            )


def test_create_device_missing_driver():
    """Missing 'selected_driver' raises McpError."""
    with patch("tools.devicehub_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(create_devicehub_device(_make_request(), {"name": "Dev"}))


def test_create_device_invalid_driver():
    """Unknown driver raises McpError with available drivers listed."""

    async def fake_cli(request, function, args):
        assert function == "le.devicehub.ListDrivers"
        return [{"ID": "drv-1", "Name": "ModbusTCP"}]

    with patch.object(devicehub_tools, "run_cli_function", side_effect=fake_cli):
        with pytest.raises(McpError) as exc_info:
            _run(
                create_devicehub_device(
                    _make_request(), {"name": "Dev", "selected_driver": "BadDriver"}
                )
            )

    assert "not found" in str(exc_info.value).lower()
    assert "ModbusTCP" in str(exc_info.value)


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

    result = _run(
        get_devicehub_device_tags(_make_request(), {"device_name": "TestDevice"})
    )
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
        {
            "data": {
                "ListRegistersFromAllDevices": {"Registers": [{"TagName": "Pressure"}]}
            }
        },
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


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.devices.list_devices")
@patch("tools.devicehub_tools.api.gql_query")
def test_get_device_tags_over_limit_returns_first_page(
    mock_gql_query, mock_list_devices, mock_connection
):
    """Totals beyond the page limit no longer refuse: the first page comes
    back with pagination metadata."""
    devicehub_tools._device_list_cache.clear()
    mock_connection.return_value = MagicMock()
    dev = MagicMock()
    dev.name = "BigDevice"
    dev.id = "dev-big"
    mock_list_devices.return_value = [dev]
    page = [{"TagName": f"Tag{i:04d}", "ID": f"t{i}"} for i in range(1000)]
    mock_gql_query.side_effect = [
        {"data": {"ListRegisters": {"TotalCount": 2500}}},
        {"data": {"ListRegisters": {"Registers": page}}},
    ]

    data = _parse(
        _run(get_devicehub_device_tags(_make_request(), {"device_name": "BigDevice"}))
    )

    assert data["success"] is True
    assert data["total_count"] == 2500
    assert data["count"] == 1000
    assert data["has_more"] is True
    assert data["next_offset"] == 1000
    # first page: SkipCount omitted entirely for older-LE schema compatibility
    list_input = mock_gql_query.call_args_list[1].args[1]["variables"]["input"]
    assert list_input["Limit"] == 1000
    assert "SkipCount" not in list_input


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.devices.list_devices")
@patch("tools.devicehub_tools.api.gql_query")
def test_get_device_tags_offset_passed_as_skipcount(
    mock_gql_query, mock_list_devices, mock_connection
):
    """limit/offset arguments reach the GraphQL input as Limit/SkipCount."""
    devicehub_tools._device_list_cache.clear()
    mock_connection.return_value = MagicMock()
    dev = MagicMock()
    dev.name = "BigDevice"
    dev.id = "dev-big"
    mock_list_devices.return_value = [dev]
    mock_gql_query.side_effect = [
        {"data": {"ListRegisters": {"TotalCount": 2500}}},
        {"data": {"ListRegisters": {"Registers": [{"TagName": "Tag2000"}]}}},
    ]

    data = _parse(
        _run(
            get_devicehub_device_tags(
                _make_request(),
                {"device_name": "BigDevice", "limit": 500, "offset": 2000},
            )
        )
    )

    assert data["success"] is True
    assert data["offset"] == 2000
    list_input = mock_gql_query.call_args_list[1].args[1]["variables"]["input"]
    assert list_input["Limit"] == 500
    assert list_input["SkipCount"] == 2000


@patch("tools.devicehub_tools.get_litmus_connection")
@patch("tools.devicehub_tools.api.gql_query")
def test_get_all_tags_over_limit_pages_instead_of_refusing(
    mock_gql_query, mock_connection
):
    """All-devices path also paginates with SkipCount."""
    mock_connection.return_value = MagicMock()
    mock_gql_query.side_effect = [
        {"data": {"ListRegistersFromAllDevices": {"TotalCount": 1500}}},
        {
            "data": {
                "ListRegistersFromAllDevices": {
                    "Registers": [{"TagName": "Pressure"}]
                }
            }
        },
    ]

    data = _parse(
        _run(get_devicehub_device_tags(_make_request(), {"offset": 1000}))
    )

    assert data["success"] is True
    assert data["total_count"] == 1500
    list_input = mock_gql_query.call_args_list[1].args[1]["variables"]["input"]
    assert list_input["SkipCount"] == 1000


def test_get_device_tags_rejects_bad_pagination_args():
    for bad in ({"limit": 0}, {"limit": 1001}, {"offset": -5}, {"limit": "abc"}):
        with pytest.raises(McpError):
            _run(get_devicehub_device_tags(_make_request(), bad))


# ── get_current_value_of_devicehub_tag ─────────────────────────────────────


def test_get_tag_value_missing_device_name():
    """Missing 'device_name' raises McpError."""
    with patch("tools.devicehub_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(
                get_current_value_of_devicehub_tag(
                    _make_request(), {"tag_name": "Temp"}
                )
            )


def test_get_tag_value_missing_both_identifiers():
    """Missing both tag_name and tag_id raises McpError."""
    with patch("tools.devicehub_tools.get_litmus_connection"):
        with pytest.raises(McpError):
            _run(
                get_current_value_of_devicehub_tag(
                    _make_request(), {"device_name": "Dev"}
                )
            )


def _make_device(name="TestDevice", device_id="d-1"):
    device = MagicMock()
    device.name = name
    device.id = device_id
    return device


def _make_tag(tag_name="Temp", tag_id="t-1", topic="dh.raw.d-1.Temp"):
    tag = MagicMock()
    tag.tag_name = tag_name
    tag.id = tag_id
    tp = MagicMock()
    tp.direction = "Output"
    tp.topic = topic
    tag.topics = [tp]
    return tag


def _make_influx_result(points):
    rs = MagicMock()
    rs.get_points.return_value = points
    return rs


def _influx_query_dispatch(behavior, measurements=()):
    """Build a client.query side effect: SHOW MEASUREMENTS returns the given
    measurement names, everything else hits `behavior` (a result or a
    callable taking the query string)."""

    def side_effect(q, *args, **kwargs):
        if q.strip().upper().startswith("SHOW MEASUREMENTS"):
            return _make_influx_result([{"name": n} for n in measurements])
        # Mock result objects are callable too; only dispatch real functions.
        if callable(behavior) and not isinstance(behavior, Mock):
            return behavior(q)
        return behavior

    return side_effect


@patch("tools.devicehub_tools.get_current_value_on_topic", new_callable=AsyncMock)
@patch("tools.devicehub_tools.tags.list_registers_from_single_device")
@patch("tools.devicehub_tools.devices.list_devices")
@patch("tools.devicehub_tools.get_litmus_connection")
def test_get_tag_value_passes_le_connection_to_sdk(
    mock_connection, mock_list_devices, mock_list_registers, mock_get_value
):
    """Regression: tag listing must use the header-derived connection.

    Omitting le_connection makes litmussdk fall back to its env-based
    DEFAULT_LE_CONNECTION, which validates certificates by default and
    fails against edges with self-signed certs.
    """
    devicehub_tools._device_list_cache.clear()
    connection = MagicMock()
    mock_connection.return_value = connection
    device = _make_device()
    mock_list_devices.return_value = [device]
    mock_list_registers.return_value = [_make_tag()]
    mock_get_value.return_value = {"value": 42}

    result = _run(
        get_current_value_of_devicehub_tag(
            _make_request(), {"device_name": "TestDevice", "tag_name": "Temp"}
        )
    )
    data = _parse(result)

    assert data["success"] is True
    mock_list_registers.assert_called_once_with(device, le_connection=connection)


# ── get_device_connection_status ────────────────────────────────────────────


@patch("tools.devicehub_tools._make_influx_client")
@patch("tools.devicehub_tools.get_influx_connection_params")
@patch("tools.devicehub_tools.tags.list_registers_from_single_device")
@patch("tools.devicehub_tools.devices.list_devices")
@patch("tools.devicehub_tools.get_litmus_connection")
def test_connection_status_recent_data_is_connected(
    mock_connection,
    mock_list_devices,
    mock_list_registers,
    mock_influx_params,
    mock_make_client,
):
    """Regression: a device with fresh data must report 'connected'.

    The old SELECT last(*) query returned the epoch-0 timestamp (InfluxQL
    behavior for selectors applied to multiple fields), so every device
    with data was reported stale with last_seen 1970-01-01.
    """
    mock_connection.return_value = MagicMock()
    mock_list_devices.return_value = [_make_device()]
    mock_list_registers.return_value = [_make_tag()]
    mock_influx_params.return_value = {}

    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    client = MagicMock()
    client.query.side_effect = _influx_query_dispatch(
        _make_influx_result([{"time": recent, "value": 1.0}])
    )
    mock_make_client.return_value = client

    result = _run(get_device_connection_status(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    dev = data["devices"][0]
    assert dev["status"] == "connected"
    assert dev["last_seen"] == recent

    query = client.query.call_args[0][0]
    assert "last(*)" not in query
    assert "ORDER BY time DESC LIMIT 1" in query


@patch("tools.devicehub_tools._make_influx_client")
@patch("tools.devicehub_tools.get_influx_connection_params")
@patch("tools.devicehub_tools.tags.list_registers_from_single_device")
@patch("tools.devicehub_tools.devices.list_devices")
@patch("tools.devicehub_tools.get_litmus_connection")
def test_connection_status_old_data_is_stale(
    mock_connection,
    mock_list_devices,
    mock_list_registers,
    mock_influx_params,
    mock_make_client,
):
    """Data older than the threshold reports 'stale' with the real timestamp."""
    mock_connection.return_value = MagicMock()
    mock_list_devices.return_value = [_make_device()]
    mock_list_registers.return_value = [_make_tag()]
    mock_influx_params.return_value = {}

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    client = MagicMock()
    client.query.side_effect = _influx_query_dispatch(
        _make_influx_result([{"time": old, "value": 1.0}])
    )
    mock_make_client.return_value = client

    result = _run(get_device_connection_status(_make_request(), {}))
    data = _parse(result)

    dev = data["devices"][0]
    assert dev["status"] == "stale"
    assert dev["last_seen"] == old


@patch("tools.devicehub_tools._make_influx_client")
@patch("tools.devicehub_tools.get_influx_connection_params")
@patch("tools.devicehub_tools.tags.list_registers_from_single_device")
@patch("tools.devicehub_tools.devices.list_devices")
@patch("tools.devicehub_tools.get_litmus_connection")
def test_connection_status_checks_all_topics(
    mock_connection,
    mock_list_devices,
    mock_list_registers,
    mock_influx_params,
    mock_make_client,
):
    """Regression: all output topics are probed, not just the first tag's.

    A device whose first tag has no stored data must still report
    'connected' when another tag is flowing.
    """
    mock_connection.return_value = MagicMock()
    mock_list_devices.return_value = [_make_device()]
    mock_list_registers.return_value = [
        _make_tag(tag_name="Empty", tag_id="t-1", topic="dh.raw.d-1.Empty"),
        _make_tag(tag_name="Flowing", tag_id="t-2", topic="dh.raw.d-1.Flowing"),
    ]
    mock_influx_params.return_value = {}

    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def query_behavior(q):
        if "Flowing" in q:
            return _make_influx_result([{"time": recent, "value": 1.0}])
        return _make_influx_result([])

    client = MagicMock()
    client.query.side_effect = _influx_query_dispatch(query_behavior)
    mock_make_client.return_value = client

    result = _run(get_device_connection_status(_make_request(), {}))
    data = _parse(result)

    dev = data["devices"][0]
    assert dev["status"] == "connected"
    assert dev["checked_topic"] == "dh.raw.d-1.Flowing"
    assert dev["checked_topics_count"] == 2
    select_calls = [
        c for c in client.query.call_args_list if c.args[0].startswith("SELECT")
    ]
    assert len(select_calls) == 2


@patch("tools.devicehub_tools._make_influx_client")
@patch("tools.devicehub_tools.get_influx_connection_params")
@patch("tools.devicehub_tools.tags.list_registers_from_single_device")
@patch("tools.devicehub_tools.devices.list_devices")
@patch("tools.devicehub_tools.get_litmus_connection")
def test_connection_status_surfaces_errors(
    mock_connection,
    mock_list_devices,
    mock_list_registers,
    mock_influx_params,
    mock_make_client,
):
    """Regression: failures are reported per device instead of silently
    collapsing into 'no_data'."""
    mock_connection.return_value = MagicMock()
    mock_list_devices.return_value = [_make_device()]
    mock_list_registers.side_effect = RuntimeError("boom")
    mock_influx_params.return_value = {}
    mock_make_client.return_value = MagicMock()

    result = _run(get_device_connection_status(_make_request(), {}))
    data = _parse(result)

    dev = data["devices"][0]
    assert dev["status"] == "no_data"
    assert dev["error"].startswith("tag_listing_failed")


@patch("tools.devicehub_tools._make_influx_client")
@patch("tools.devicehub_tools.get_influx_connection_params")
@patch("tools.devicehub_tools.tags.list_registers_from_single_device")
@patch("tools.devicehub_tools.devices.list_devices")
@patch("tools.devicehub_tools.get_litmus_connection")
def test_connection_status_no_points_is_no_data(
    mock_connection,
    mock_list_devices,
    mock_list_registers,
    mock_influx_params,
    mock_make_client,
):
    """No stored points on any topic reports 'no_data' without error."""
    mock_connection.return_value = MagicMock()
    mock_list_devices.return_value = [_make_device()]
    mock_list_registers.return_value = [_make_tag()]
    mock_influx_params.return_value = {}

    client = MagicMock()
    client.query.return_value = _make_influx_result([])
    mock_make_client.return_value = client

    result = _run(get_device_connection_status(_make_request(), {}))
    data = _parse(result)

    dev = data["devices"][0]
    assert dev["status"] == "no_data"
    assert dev["last_seen"] is None
    assert "error" not in dev


# ── get_tag_statistics ──────────────────────────────────────────────────────


@patch("tools.data_tools._make_influx_client")
@patch("tools.data_tools.get_influx_connection_params")
@patch("tools.data_tools.dh_tags.list_registers_from_single_device")
@patch("tools.data_tools.dh_devices.list_devices")
@patch("tools.data_tools.get_litmus_connection")
def test_tag_statistics_strips_epoch_zero_timestamp(
    mock_connection,
    mock_list_devices,
    mock_list_registers,
    mock_influx_params,
    mock_make_client,
):
    """Regression: aggregate queries return the epoch-0 timestamp, which must
    not be surfaced in the statistics as if it were a data timestamp."""
    from tools.data_tools import get_tag_statistics

    mock_connection.return_value = MagicMock()
    mock_list_devices.return_value = [_make_device()]
    mock_list_registers.return_value = [_make_tag()]
    mock_influx_params.return_value = {}

    client = MagicMock()
    client.query.side_effect = _influx_query_dispatch(
        _make_influx_result(
            [
                {
                    "time": "1970-01-01T00:00:00Z",
                    "mean": 5.0,
                    "min": 1.0,
                    "max": 9.0,
                    "count": 100,
                    "stddev": 2.0,
                }
            ]
        )
    )
    mock_make_client.return_value = client

    result = _run(
        get_tag_statistics(
            _make_request(), {"device_name": "TestDevice", "tag_name": "Temp"}
        )
    )
    data = _parse(result)

    assert data["success"] is True
    stats = data["statistics"]
    assert "time" not in stats
    assert stats["mean"] == 5.0
    assert stats["baseline_low"] == 1.0
    assert stats["baseline_high"] == 9.0


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

    result = _run(
        set_litmusedge_friendly_name(_make_request(), {"new_friendly_name": "NewName"})
    )
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
