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
    lem_dashboard_usage_tool,
    lem_get_device_details_tool,
    lem_get_expired_licenses_tool,
    lem_get_license_expiry_tool,
    lem_get_project_alerts_tool,
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
    mock_sdk.return_value = {"items": [{"id": "d1"}], "total": 1}

    result = _run(lem_list_devices_tool(_make_request(), {"limit": 5}))
    data = _parse(result)

    assert data["success"] is True
    assert data["page"]["total"] == 1
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
