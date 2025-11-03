"""
Tests for Litmus MCP Server Tools

This test suite covers all MCP tools with both success and error scenarios.
Uses pytest with mocking to test stateless, header-based authentication.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from starlette.requests import Request

# Import the tools from server
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from server import (
    get_litmusedge_driver_list,
    get_devicehub_devices,
    get_devicehub_device_tags,
    get_current_value_of_devicehub_tag,
    create_devicehub_device,
    get_litmusedge_friendly_name,
    set_litmusedge_friendly_name,
    get_cloud_activation_status,
    get_all_containers_on_litmusedge,
    run_docker_container_on_litmusedge,
    get_current_value_on_topic,
    get_multiple_values_from_topic,
)


# ==================== Fixtures ====================


@pytest.fixture
def mock_request():
    """Create a mock MCP request with valid headers"""
    request = Mock(spec=Request)
    request.headers = {
        "EDGE_URL": "https://test-edge.local:8443",
        "EDGE_API_CLIENT_ID": "test-client-id",
        "EDGE_API_CLIENT_SECRET": "test-secret",
        "VALIDATE_CERTIFICATE": "false",
    }
    return request


@pytest.fixture
def mock_request_missing_headers():
    """Create a mock request with missing required headers"""
    request = Mock(spec=Request)
    request.headers = {
        "EDGE_URL": "https://test-edge.local:8443",
        # Missing CLIENT_ID and SECRET
    }
    return request


@pytest.fixture
def mock_connection():
    """Create a mock Litmus Edge connection"""
    return MagicMock()


# ==================== Test: get_litmusedge_driver_list ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.list_all_drivers")
def test_get_litmusedge_driver_list_success(
    mock_list_drivers, mock_get_creds, mock_request
):
    """Test successfully retrieving driver list"""
    # Setup mocks
    mock_get_creds.return_value = MagicMock()

    # Create mock driver objects
    mock_driver1 = MagicMock()
    mock_driver1.name = "ModbusTCP"
    mock_driver2 = MagicMock()
    mock_driver2.name = "OPCUA"

    mock_list_drivers.return_value = [mock_driver1, mock_driver2]

    # Execute
    result = get_litmusedge_driver_list(mock_request)

    # Verify
    assert isinstance(result, list)
    assert len(result) >= 2
    mock_get_creds.assert_called_once_with(mock_request)


def test_get_litmusedge_driver_list_missing_headers(mock_request_missing_headers):
    """Test driver list with missing authentication headers"""
    with pytest.raises(Exception):  # Should raise McpError
        get_litmusedge_driver_list(mock_request_missing_headers)


# ==================== Test: get_devicehub_devices ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.devices.list_devices")
def test_get_devicehub_devices_success(mock_list_devices, mock_get_creds, mock_request):
    """Test successfully retrieving devices"""
    # Setup mocks
    mock_get_creds.return_value = MagicMock()

    mock_device = MagicMock()
    mock_device.name = "TestDevice"
    mock_device.__dict__ = {
        "name": "TestDevice",
        "driver": "ModbusTCP",
        "enabled": True,
    }

    mock_list_devices.return_value = [mock_device]

    # Execute
    result = get_devicehub_devices(mock_request)

    # Verify
    assert isinstance(result, dict)
    assert "TestDevice" in result
    assert result["TestDevice"]["name"] == "TestDevice"
    mock_get_creds.assert_called_once_with(mock_request)


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.devices.list_devices")
def test_get_devicehub_devices_empty(mock_list_devices, mock_get_creds, mock_request):
    """Test retrieving devices when none exist"""
    mock_get_creds.return_value = MagicMock()
    mock_list_devices.return_value = []

    result = get_devicehub_devices(mock_request)

    assert isinstance(result, dict)
    assert len(result) == 0


# ==================== Test: get_devicehub_device_tags ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.devices.list_devices")
@patch("server.tags.list_registers_from_single_device")
def test_get_devicehub_device_tags_success(
    mock_list_tags, mock_list_devices, mock_get_creds, mock_request
):
    """Test successfully retrieving device tags"""
    # Setup mocks
    mock_get_creds.return_value = MagicMock()

    mock_device = MagicMock()
    mock_device.name = "TestDevice"
    mock_list_devices.return_value = [mock_device]

    mock_tag = MagicMock()
    mock_tag.tag_name = "Temperature"
    mock_tag.__dict__ = {
        "tag_name": "Temperature",
        "address": "40001",
        "data_type": "FLOAT",
    }
    mock_list_tags.return_value = [mock_tag]

    # Execute
    result = get_devicehub_device_tags(mock_request, "TestDevice")

    # Verify
    assert isinstance(result, dict)
    assert "Temperature" in result
    assert result["Temperature"]["tag_name"] == "Temperature"


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.devices.list_devices")
def test_get_devicehub_device_tags_device_not_found(
    mock_list_devices, mock_get_creds, mock_request
):
    """Test error when device doesn't exist"""
    mock_get_creds.return_value = MagicMock()
    mock_list_devices.return_value = []

    with pytest.raises(Exception) as exc_info:
        get_devicehub_device_tags(mock_request, "NonExistentDevice")

    assert "not found" in str(exc_info.value).lower()


# ==================== Test: get_current_value_of_devicehub_tag ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.devices.list_devices")
@patch("server.tags.list_registers_from_single_device")
@patch("server.asyncio.run")
def test_get_current_value_of_devicehub_tag_success(
    mock_asyncio_run, mock_list_tags, mock_list_devices, mock_get_creds, mock_request
):
    """Test successfully reading tag value"""
    # Setup mocks
    mock_get_creds.return_value = MagicMock()

    mock_device = MagicMock()
    mock_device.name = "TestDevice"
    mock_list_devices.return_value = [mock_device]

    mock_topic = MagicMock()
    mock_topic.direction = "Output"
    mock_topic.topic = "test/topic/output"

    mock_tag = MagicMock()
    mock_tag.tag_name = "Temperature"
    mock_tag.topics = [mock_topic]
    mock_list_tags.return_value = [mock_tag]

    mock_asyncio_run.return_value = {
        "value": 25.5,
        "timestamp": 1234567890,
        "quality": "good",
    }

    # Execute
    result = get_current_value_of_devicehub_tag(
        mock_request, "TestDevice", tag_name="Temperature"
    )

    # Verify
    assert isinstance(result, dict)
    assert result["value"] == 25.5
    assert "timestamp" in result


@patch("server._get_litmus_creds_from_mcp_client_headers")
def test_get_current_value_of_devicehub_tag_missing_params(
    mock_get_creds, mock_request
):
    """Test error when neither tag_name nor tag_id provided"""
    mock_get_creds.return_value = MagicMock()

    with pytest.raises(Exception) as exc_info:
        get_current_value_of_devicehub_tag(mock_request, "TestDevice")

    assert "tag_name or tag_id is required" in str(exc_info.value).lower()


# ==================== Test: create_devicehub_device ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.list_all_drivers")
@patch("server.devices.create_device")
def test_create_devicehub_device_success(
    mock_create_device, mock_list_drivers, mock_get_creds, mock_request
):
    """Test successfully creating a device"""
    # Setup mocks
    mock_get_creds.return_value = MagicMock()

    mock_driver = MagicMock()
    mock_driver.name = "ModbusTCP"
    mock_driver.id = "driver-123"
    mock_driver.get_default_properties.return_value = {"ip": "192.168.1.1", "port": 502}
    mock_list_drivers.return_value = [mock_driver]

    mock_created = MagicMock()
    mock_created.__dict__ = {"id": "device-456", "name": "NewDevice"}
    mock_create_device.return_value = mock_created

    # Execute
    result = create_devicehub_device(mock_request, "NewDevice", "ModbusTCP")

    # Verify
    assert isinstance(result, dict)
    assert result["name"] == "NewDevice"


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.list_all_drivers")
def test_create_devicehub_device_invalid_driver(
    mock_list_drivers, mock_get_creds, mock_request
):
    """Test error with invalid driver name"""
    mock_get_creds.return_value = MagicMock()

    mock_driver = MagicMock()
    mock_driver.name = "ModbusTCP"
    mock_list_drivers.return_value = [mock_driver]

    with pytest.raises(Exception) as exc_info:
        create_devicehub_device(mock_request, "NewDevice", "InvalidDriver")

    assert "not found" in str(exc_info.value).lower()


# ==================== Test: get_litmusedge_friendly_name ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.network.get_friendly_name")
def test_get_litmusedge_friendly_name_success(
    mock_get_name, mock_get_creds, mock_request
):
    """Test successfully getting device friendly name"""
    mock_get_creds.return_value = MagicMock()
    mock_get_name.return_value = "Factory_Gateway_01"

    result = get_litmusedge_friendly_name(mock_request)

    assert result == "Factory_Gateway_01"
    assert isinstance(result, str)


# ==================== Test: set_litmusedge_friendly_name ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.network.set_friendly_name")
def test_set_litmusedge_friendly_name_success(
    mock_set_name, mock_get_creds, mock_request
):
    """Test successfully setting device friendly name"""
    mock_get_creds.return_value = MagicMock()
    mock_set_name.return_value = None

    result = set_litmusedge_friendly_name(mock_request, "New_Gateway_Name")

    assert "updated" in result.lower()
    assert "New_Gateway_Name" in result
    mock_set_name.assert_called_once()


# ==================== Test: get_cloud_activation_status ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.device_management.show_cloud_registration_status")
def test_get_cloud_activation_status_success(
    mock_get_status, mock_get_creds, mock_request
):
    """Test successfully getting cloud activation status"""
    mock_get_creds.return_value = MagicMock()
    mock_get_status.return_value = {
        "status": "activated",
        "connected": True,
        "last_sync": "2025-01-15T10:30:00Z",
    }

    result = get_cloud_activation_status(mock_request)

    assert isinstance(result, dict)
    assert result["status"] == "activated"
    assert result["connected"] is True


# ==================== Test: get_all_containers_on_litmusedge ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.list_all_containers")
def test_get_all_containers_success(mock_list_containers, mock_get_creds, mock_request):
    """Test successfully listing containers"""
    mock_get_creds.return_value = MagicMock()
    mock_list_containers.return_value = [
        {"name": "node-red", "image": "nodered/node-red:latest", "status": "running"},
        {"name": "influxdb", "image": "influxdb:2.0", "status": "running"},
    ]

    result = get_all_containers_on_litmusedge(mock_request)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["name"] == "node-red"


# ==================== Test: run_docker_container_on_litmusedge ====================


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.run_container")
def test_run_docker_container_success(mock_run_container, mock_get_creds, mock_request):
    """Test successfully running a docker container"""
    mock_get_creds.return_value = MagicMock()
    mock_run_container.return_value = {"id": "container-abc123"}

    result = run_docker_container_on_litmusedge(
        mock_request, "docker run -d --name test-app nginx:latest"
    )

    assert isinstance(result, str)
    assert "container-abc123" in result


@patch("server._get_litmus_creds_from_mcp_client_headers")
@patch("server.run_container")
def test_run_docker_container_no_id(mock_run_container, mock_get_creds, mock_request):
    """Test running container when no ID returned"""
    mock_get_creds.return_value = MagicMock()
    mock_run_container.return_value = {}

    result = run_docker_container_on_litmusedge(mock_request, "docker run nginx")

    assert "Unknown" in result or "container" in result.lower()


# ==================== Test: get_current_value_on_topic (async) ====================


@pytest.mark.asyncio
@patch("server.nc_single_topic")
async def test_get_current_value_on_topic_success(mock_nc_single):
    """Test successfully getting value from NATS topic"""
    mock_nc_single.return_value = {
        "value": 42.5,
        "timestamp": 1234567890,
        "quality": "good",
    }

    result = await get_current_value_on_topic("test/topic")

    assert isinstance(result, dict)
    assert result["value"] == 42.5
    assert "timestamp" in result


@pytest.mark.asyncio
async def test_get_current_value_on_topic_with_custom_source():
    """Test getting value with custom NATS source"""
    with patch("server.nc_single_topic") as mock_nc_single:
        mock_nc_single.return_value = {"value": 100}

        result = await get_current_value_on_topic(
            "custom/topic", nats_source="192.168.1.100", nats_port="4223"
        )

        assert result["value"] == 100


# ==================== Test: get_multiple_values_from_topic (async) ====================


@pytest.mark.asyncio
@patch("server.collect_multiple_values_from_topic")
async def test_get_multiple_values_from_topic_success(mock_collect):
    """Test successfully collecting multiple values"""
    mock_collect.return_value = {
        "values": [10.0, 20.0, 30.0, 40.0, 50.0],
        "humanTimestamps": [
            "2025-01-15 10:00:00",
            "2025-01-15 10:00:01",
            "2025-01-15 10:00:02",
            "2025-01-15 10:00:03",
            "2025-01-15 10:00:04",
        ],
    }

    result = await get_multiple_values_from_topic("test/topic", num_samples=5)

    assert isinstance(result, dict)
    assert "values" in result
    assert "humanTimestamps" in result
    assert len(result["values"]) == 5
    assert len(result["humanTimestamps"]) == 5


@pytest.mark.asyncio
async def test_get_multiple_values_from_topic_default_samples():
    """Test collecting values with default sample count"""
    with patch("server.collect_multiple_values_from_topic") as mock_collect:
        mock_collect.return_value = {"values": [0] * 10, "humanTimestamps": [""] * 10}

        result = await get_multiple_values_from_topic("test/topic")

        # Default should be 10 samples
        assert len(result["values"]) == 10


# ==================== Integration-Style Tests ====================


def test_full_authentication_flow_with_valid_headers(mock_request):
    """Test that valid headers allow tool execution"""
    with patch("server._get_litmus_creds_from_mcp_client_headers") as mock_get_creds:
        with patch("server.list_all_drivers") as mock_list_drivers:
            mock_get_creds.return_value = MagicMock()
            mock_driver = MagicMock()
            mock_driver.name = "Test"
            mock_list_drivers.return_value = [mock_driver]

            # Should not raise any exception
            result = get_litmusedge_driver_list(mock_request)
            assert result is not None


def test_full_authentication_flow_with_missing_headers(mock_request_missing_headers):
    """Test that missing headers prevent tool execution"""
    with pytest.raises(Exception):
        get_litmusedge_driver_list(mock_request_missing_headers)


# ==================== Run Tests ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
