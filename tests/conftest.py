"""
Pytest configuration and shared fixtures for Litmus MCP Server tests
"""

import pytest
import sys
import os
from unittest.mock import Mock, MagicMock

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def valid_edge_headers():
    """Standard valid Edge authentication headers"""
    return {
        "EDGE_URL": "https://test-edge.local:8443",
        "EDGE_API_CLIENT_ID": "test-client-id",
        "EDGE_API_CLIENT_SECRET": "test-secret-key",
        "VALIDATE_CERTIFICATE": "false",
    }


@pytest.fixture
def invalid_edge_headers():
    """Headers missing required authentication fields"""
    return {
        "EDGE_URL": "https://test-edge.local:8443",
        # Missing CLIENT_ID and SECRET
    }


@pytest.fixture
def mock_request_factory():
    """Factory to create mock requests with custom headers"""
    def _create_request(headers=None):
        from starlette.requests import Request
        request = Mock(spec=Request)
        request.headers = headers or {}
        return request
    return _create_request


@pytest.fixture
def mock_litmus_connection():
    """Mock Litmus Edge connection object"""
    return MagicMock()


@pytest.fixture
def mock_device():
    """Mock device object with standard attributes"""
    device = MagicMock()
    device.name = "TestDevice"
    device.driver = "ModbusTCP"
    device.enabled = True
    device.id = "device-123"
    device.__dict__ = {
        "name": "TestDevice",
        "driver": "ModbusTCP",
        "enabled": True,
        "id": "device-123",
        "properties": {"ip": "192.168.1.10", "port": 502}
    }
    return device


@pytest.fixture
def mock_tag():
    """Mock tag object with standard attributes"""
    tag = MagicMock()
    tag.tag_name = "Temperature"
    tag.id = "tag-456"
    tag.address = "40001"
    tag.data_type = "FLOAT"

    # Mock topic
    topic = MagicMock()
    topic.direction = "Output"
    topic.topic = "devicehub/device-123/tag-456/output"
    tag.topics = [topic]

    tag.__dict__ = {
        "tag_name": "Temperature",
        "id": "tag-456",
        "address": "40001",
        "data_type": "FLOAT",
        "scaling": {"min": 0, "max": 100},
        "unit": "°C"
    }
    return tag


@pytest.fixture
def mock_driver():
    """Mock driver object"""
    driver = MagicMock()
    driver.name = "ModbusTCP"
    driver.id = "driver-789"
    driver.get_default_properties.return_value = {
        "ip": "192.168.1.1",
        "port": 502,
        "slave_id": 1,
        "timeout": 5000
    }
    return driver


@pytest.fixture
def sample_nats_message():
    """Sample NATS message payload"""
    return {
        "value": 23.5,
        "timestamp": 1705315200000,  # 2024-01-15 10:00:00 UTC
        "quality": "good",
        "unit": "°C",
        "tag_id": "tag-456",
        "device_id": "device-123"
    }


@pytest.fixture
def sample_container_list():
    """Sample container list response"""
    return [
        {
            "id": "container-1",
            "name": "node-red",
            "image": "nodered/node-red:latest",
            "status": "running",
            "ports": ["1880:1880"],
            "created": "2025-01-10T10:00:00Z"
        },
        {
            "id": "container-2",
            "name": "influxdb",
            "image": "influxdb:2.0",
            "status": "running",
            "ports": ["8086:8086"],
            "created": "2025-01-10T11:00:00Z"
        }
    ]


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line(
        "markers", "unit: Unit tests that mock all dependencies"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests that may require external services"
    )
    config.addinivalue_line(
        "markers", "slow: Tests that take longer to execute"
    )
    config.addinivalue_line(
        "markers", "auth: Tests related to authentication and authorization"
    )
