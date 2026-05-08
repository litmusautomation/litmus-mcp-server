"""
Tests for LEM (Litmus Edge Manager) tools and the LEM connection helper.

All tests mock the SDK so no live LEM tenant is needed.
"""

import asyncio
import json
import os
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp.shared.exceptions import McpError  # noqa: E402

from utils.auth import (  # noqa: E402
    _default_admin_url,
    get_lem_connection,
    get_lem_project_id,
)
from tools.lem_tools import (  # noqa: E402
    lem_bridge_get_le_info_tool,
    lem_bridge_list_devicehub_devices_tool,
    lem_dashboard_usage_tool,
    lem_deployment_info_tool,
    lem_get_company_details_tool,
    lem_get_device_details_tool,
    lem_get_expired_licenses_tool,
    lem_get_license_expiry_tool,
    lem_get_project_alerts_tool,
    lem_get_project_details_tool,
    lem_get_system_time_tool,
    lem_list_companies_tool,
    lem_list_company_projects_tool,
    lem_list_device_groups_tool,
    lem_list_device_versions_tool,
    lem_list_devices_tool,
)


_LEM_HEADERS = {
    "EDGE_MANAGER_URL": "https://lem.example.com",
    "EDGE_API_TOKEN": "tok-123",
    "EDGE_MANAGER_PROJECT_ID": "proj-1",
    "VALIDATE_CERTIFICATE": "false",
}


def _make_request(headers=None):
    request = Mock(spec=Request)
    request.headers = headers if headers is not None else dict(_LEM_HEADERS)
    return request


def _run(coro):
    return asyncio.run(coro)


def _parse(result):
    return json.loads(result[0].text)


# ── _default_admin_url ──────────────────────────────────────────────────────


def test_default_admin_url_with_https():
    assert (
        _default_admin_url("https://lem.example.com")
        == "https://lem.example.com:8446"
    )


def test_default_admin_url_replaces_existing_port():
    assert (
        _default_admin_url("https://lem.example.com:443")
        == "https://lem.example.com:8446"
    )


def test_default_admin_url_with_bare_host():
    assert _default_admin_url("lem.example.com") == "https://lem.example.com:8446"


def test_default_admin_url_with_path_strips_path():
    # URL host comes from hostname, not path
    assert (
        _default_admin_url("https://lem.example.com/api/v1")
        == "https://lem.example.com:8446"
    )


# ── get_lem_connection ──────────────────────────────────────────────────────


@patch("utils.auth.new_lem_connection")
def test_lem_connection_with_valid_headers(mock_new):
    mock_new.return_value = MagicMock()
    request = _make_request()

    conn = get_lem_connection(request)

    assert conn is mock_new.return_value
    mock_new.assert_called_once()
    kwargs = mock_new.call_args.kwargs
    assert kwargs["edge_manager_url"] == "https://lem.example.com"
    assert kwargs["edge_api_token"] == "tok-123"
    assert kwargs["edge_manager_admin_url"] == "https://lem.example.com:8446"
    assert kwargs["validate_certificate"] is False


@patch("utils.auth.new_lem_connection")
def test_lem_connection_uses_explicit_admin_url(mock_new):
    mock_new.return_value = MagicMock()
    headers = dict(_LEM_HEADERS)
    headers["EDGE_MANAGER_ADMIN_URL"] = "https://admin.lem.example.com:9000"
    request = _make_request(headers)

    get_lem_connection(request)

    assert (
        mock_new.call_args.kwargs["edge_manager_admin_url"]
        == "https://admin.lem.example.com:9000"
    )


def test_lem_connection_missing_manager_url_raises():
    headers = dict(_LEM_HEADERS)
    headers.pop("EDGE_MANAGER_URL")
    with pytest.raises(McpError) as exc:
        get_lem_connection(_make_request(headers))
    assert "EDGE_MANAGER_URL" in str(exc.value)


def test_lem_connection_missing_token_raises():
    headers = dict(_LEM_HEADERS)
    headers.pop("EDGE_API_TOKEN")
    with pytest.raises(McpError) as exc:
        get_lem_connection(_make_request(headers))
    assert "EDGE_API_TOKEN" in str(exc.value)


# ── get_lem_project_id ──────────────────────────────────────────────────────


def test_project_id_from_argument_takes_priority():
    request = _make_request()
    assert get_lem_project_id(request, {"project_id": "explicit"}) == "explicit"


def test_project_id_falls_back_to_header():
    request = _make_request()
    assert get_lem_project_id(request, {}) == "proj-1"


def test_project_id_missing_from_both_raises():
    headers = dict(_LEM_HEADERS)
    headers.pop("EDGE_MANAGER_PROJECT_ID")
    with pytest.raises(McpError) as exc:
        get_lem_project_id(_make_request(headers), {})
    assert "project_id" in str(exc.value)


# ── lem_list_devices ────────────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_devices_paginated")
def test_lem_list_devices_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = {
        "pageNum": 0,
        "pagesCount": 1,
        "size": 5,
        "totalSize": 1,
        "elements": [{"id": "d1", "name": "edge-A"}],
    }

    result = _run(lem_list_devices_tool(_make_request(), {"limit": 5}))
    data = _parse(result)

    assert data["success"] is True
    # Normalized convenience fields surfaced at top level.
    assert data["count"] == 1
    assert data["total_size"] == 1
    assert data["devices"][0]["id"] == "d1"
    # Raw page is still nested for callers that need it.
    assert data["page"]["pagesCount"] == 1
    kwargs = mock_sdk.call_args.kwargs
    assert kwargs["project_id"] == "proj-1"
    assert kwargs["limit"] == 5
    assert kwargs["raw"] is True


@patch("tools.lem_tools.get_lem_connection")
def test_lem_list_devices_missing_project_id_raises(mock_conn):
    mock_conn.return_value = MagicMock()
    headers = dict(_LEM_HEADERS)
    headers.pop("EDGE_MANAGER_PROJECT_ID")
    with pytest.raises(McpError):
        _run(lem_list_devices_tool(_make_request(headers), {}))


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_devices_paginated")
def test_lem_list_devices_sdk_error_returns_error_payload(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.side_effect = RuntimeError("LEM 502")

    result = _run(lem_list_devices_tool(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is False
    assert data["error"] == "lem_list_devices_failed"
    assert "LEM 502" in data["message"]


# ── lem_get_device_details ──────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_current_device_details")
def test_lem_device_details_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = {"id": "dev-1", "name": "edge-A"}

    result = _run(
        lem_get_device_details_tool(_make_request(), {"device_id": "dev-1"})
    )
    data = _parse(result)

    assert data["success"] is True
    assert data["device"]["id"] == "dev-1"
    assert mock_sdk.call_args.kwargs["device_id"] == "dev-1"


@patch("tools.lem_tools.get_lem_connection")
def test_lem_device_details_missing_device_id_raises(mock_conn):
    mock_conn.return_value = MagicMock()
    with pytest.raises(McpError):
        _run(lem_get_device_details_tool(_make_request(), {}))


# ── lem_list_device_versions ────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_device_versions")
def test_lem_device_versions_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = [{"version": "3.10"}, {"version": "3.11"}]

    result = _run(lem_list_device_versions_tool(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert len(data["versions"]) == 2


# ── lem_list_device_groups ──────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_device_tags")
def test_lem_device_groups_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = ["plant-a", "plant-b"]

    result = _run(lem_list_device_groups_tool(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert data["groups"] == ["plant-a", "plant-b"]


# ── lem_get_license_expiry ──────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_license_expiry_in_x_days")
def test_lem_license_expiry_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = [{"id": "dev-1"}]

    result = _run(
        lem_get_license_expiry_tool(_make_request(), {"expiry_days": 30})
    )
    data = _parse(result)

    assert data["success"] is True
    assert data["devices"] == [{"id": "dev-1"}]
    assert mock_sdk.call_args.kwargs["expiry_days"] == 30


@patch("tools.lem_tools.get_lem_connection")
def test_lem_license_expiry_missing_days_raises(mock_conn):
    mock_conn.return_value = MagicMock()
    with pytest.raises(McpError):
        _run(lem_get_license_expiry_tool(_make_request(), {}))


# ── lem_get_expired_licenses ────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_expired_licenses")
def test_lem_expired_licenses_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = []

    result = _run(lem_get_expired_licenses_tool(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert data["devices"] == []


# ── lem_dashboard_usage ─────────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.dashboard_usage")
def test_lem_dashboard_usage_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = {"devices": 12, "alerts": 0}

    result = _run(lem_dashboard_usage_tool(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert data["usage"]["devices"] == 12


# ── lem_get_project_alerts ──────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_project_alerts")
def test_lem_project_alerts_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = [{"id": "a1", "severity": "warn"}]

    result = _run(lem_get_project_alerts_tool(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert data["alerts"][0]["id"] == "a1"


# ── lem_list_companies ──────────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.list_all_company_stats")
def test_lem_list_companies_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = [
        {"companyName": "acme", "totalNumOfProjects": 2, "totalNumOfDevices": 14},
        {"companyName": "foo", "totalNumOfProjects": 1, "totalNumOfDevices": 3},
    ]

    result = _run(lem_list_companies_tool(_make_request()))
    data = _parse(result)

    assert data["success"] is True
    assert data["count"] == 2
    assert data["companies"][0]["companyName"] == "acme"
    assert mock_sdk.call_args.kwargs["raw"] is True


# ── lem_get_company_details ─────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_company_details")
def test_lem_company_details_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = {"name": "acme", "realName": "Acme Corp"}

    result = _run(
        lem_get_company_details_tool(_make_request(), {"company_name": "acme"})
    )
    data = _parse(result)

    assert data["success"] is True
    assert data["company"]["realName"] == "Acme Corp"
    assert mock_sdk.call_args.kwargs["company_name"] == "acme"


def test_lem_company_details_missing_name_raises():
    with pytest.raises(McpError):
        _run(lem_get_company_details_tool(_make_request(), {}))


# ── lem_list_company_projects ───────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_company_projects")
def test_lem_company_projects_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}]

    result = _run(
        lem_list_company_projects_tool(_make_request(), {"company_name": "acme"})
    )
    data = _parse(result)

    assert data["success"] is True
    assert data["count"] == 3


def test_lem_company_projects_missing_name_raises():
    with pytest.raises(McpError):
        _run(lem_list_company_projects_tool(_make_request(), {}))


# ── lem_get_project_details ─────────────────────────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_project_details")
def test_lem_project_details_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = {"id": "proj-1", "name": "production"}

    result = _run(lem_get_project_details_tool(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert data["project"]["id"] == "proj-1"


# ── lem_deployment_info / lem_get_system_time ───────────────────────────────


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.deployment_info")
def test_lem_deployment_info_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = {"version": "4.2.0", "build": "abc123"}

    result = _run(lem_deployment_info_tool(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert data["deployment"]["version"] == "4.2.0"


@patch("tools.lem_tools.get_lem_connection")
@patch("tools.lem_tools.get_system_time")
def test_lem_system_time_success(mock_sdk, mock_conn):
    mock_conn.return_value = MagicMock()
    mock_sdk.return_value = {"time": "2026-05-07T15:30:00Z"}

    result = _run(lem_get_system_time_tool(_make_request(), {}))
    data = _parse(result)

    assert data["success"] is True
    assert data["system_time"]["time"] == "2026-05-07T15:30:00Z"


# ── lem_bridge_list_devicehub_devices ───────────────────────────────────────


@patch("tools.lem_tools.new_lem_bridge_connection")
@patch("tools.lem_tools.devicehub_devices.list_devices")
def test_lem_bridge_list_devicehub_devices_success(mock_list, mock_bridge):
    mock_bridge.return_value = MagicMock()
    mock_list.return_value = [
        {
            "ID": "d1",
            "Name": "modbus-1",
            "DriverID": "drv-modbus",
            "Description": None,
        },
    ]

    result = _run(
        lem_bridge_list_devicehub_devices_tool(
            _make_request(), {"project_id": "p1", "device_id": "d1"}
        )
    )
    data = _parse(result)

    assert data["success"] is True
    assert data["count"] == 1
    assert data["devicehub_devices"][0]["name"] == "modbus-1"
    assert data["devicehub_devices"][0]["driver_id"] == "drv-modbus"
    # SDK was invoked with raw=True so quirky records don't blow up validation.
    assert mock_list.call_args.kwargs["raw"] is True
    # Bridge was built with the correct ids.
    kwargs = mock_bridge.call_args.kwargs
    assert kwargs["project_id"] == "p1"
    assert kwargs["device_id"] == "d1"


@patch("tools.lem_tools.new_lem_bridge_connection")
@patch("tools.lem_tools.devicehub_devices.list_devices")
def test_lem_bridge_list_devicehub_devices_empty_response_classified(
    mock_list, mock_bridge
):
    """An offline edge typically yields a JSON parse failure; surface it as
    edge_unreachable so the LLM can distinguish it from a real bridge bug."""
    import json

    mock_bridge.return_value = MagicMock()
    mock_list.side_effect = json.JSONDecodeError("Expecting value", "", 0)

    result = _run(
        lem_bridge_list_devicehub_devices_tool(
            _make_request(), {"project_id": "p1", "device_id": "d1"}
        )
    )
    data = _parse(result)

    assert data["success"] is False
    assert data["error"] == "lem_bridge_edge_unreachable"


def test_lem_bridge_devicehub_missing_device_id_raises():
    with pytest.raises(McpError):
        _run(
            lem_bridge_list_devicehub_devices_tool(
                _make_request(), {"project_id": "p1"}
            )
        )


def test_lem_bridge_devicehub_missing_manager_url_raises():
    headers = dict(_LEM_HEADERS)
    headers.pop("EDGE_MANAGER_URL")
    with pytest.raises(McpError):
        _run(
            lem_bridge_list_devicehub_devices_tool(
                _make_request(headers),
                {"project_id": "p1", "device_id": "d1"},
            )
        )


# ── lem_bridge_get_le_info ──────────────────────────────────────────────────


@patch("tools.lem_tools.new_lem_bridge_connection")
@patch("tools.lem_tools.network")
@patch("tools.lem_tools.device_management")
def test_lem_bridge_le_info_success(mock_dm, mock_network, mock_bridge):
    mock_bridge.return_value = MagicMock()
    mock_network.get_friendly_name.return_value = "edge-A"
    mock_dm.show_cloud_registration_status.return_value = {"activated": True}

    result = _run(
        lem_bridge_get_le_info_tool(
            _make_request(), {"project_id": "p1", "device_id": "d1"}
        )
    )
    data = _parse(result)

    assert data["success"] is True
    assert data["le_info"]["friendly_name"] == "edge-A"
    assert data["le_info"]["cloud_status"] == {"activated": True}


@patch("tools.lem_tools.new_lem_bridge_connection")
@patch("tools.lem_tools.network")
@patch("tools.lem_tools.device_management")
def test_lem_bridge_le_info_partial_failure_still_succeeds(
    mock_dm, mock_network, mock_bridge
):
    """If one sub-call fails, the other is still returned with an error field."""
    mock_bridge.return_value = MagicMock()
    mock_network.get_friendly_name.side_effect = RuntimeError("name 500")
    mock_dm.show_cloud_registration_status.return_value = {"activated": False}

    result = _run(
        lem_bridge_get_le_info_tool(
            _make_request(), {"project_id": "p1", "device_id": "d1"}
        )
    )
    data = _parse(result)

    assert data["success"] is True
    assert data["le_info"]["friendly_name_error"] == "name 500"
    assert data["le_info"]["cloud_status"] == {"activated": False}
