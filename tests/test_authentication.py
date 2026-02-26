"""
Tests for authentication and connection utilities

Tests the header-based authentication system that extracts credentials
from MCP request headers and creates isolated connections.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from starlette.requests import Request

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.auth import get_litmus_connection
from mcp.shared.exceptions import McpError


# ==================== Test: Valid Authentication ====================


@patch("utils.auth.new_le_connection")
def test_authentication_with_valid_headers(mock_new_connection, valid_edge_headers):
    """Test successful authentication with all required headers"""
    # Setup
    mock_connection = MagicMock()
    mock_new_connection.return_value = mock_connection

    request = Mock(spec=Request)
    request.headers = valid_edge_headers

    # Execute
    result = get_litmus_connection(request)

    # Verify
    assert result == mock_connection
    mock_new_connection.assert_called_once_with(
        edge_url="https://test-edge.local:8443",
        client_id="test-client-id",
        client_secret="test-secret-key",
        validate_certificate=False,
        timeout_seconds=600,
    )


@patch("utils.auth.new_le_connection")
def test_authentication_with_certificate_validation_true(mock_new_connection):
    """Test authentication with certificate validation enabled"""
    mock_new_connection.return_value = MagicMock()

    request = Mock(spec=Request)
    request.headers = {
        "EDGE_URL": "https://secure-edge.local:8443",
        "EDGE_API_CLIENT_ID": "client-id",
        "EDGE_API_CLIENT_SECRET": "secret",
        "VALIDATE_CERTIFICATE": "true",
    }

    _ = get_litmus_connection(request)

    # Verify certificate validation is True
    mock_new_connection.assert_called_once()
    call_kwargs = mock_new_connection.call_args[1]
    assert call_kwargs["validate_certificate"] is True


@patch("utils.auth.new_le_connection")
def test_authentication_with_certificate_validation_default(mock_new_connection):
    """Test authentication without certificate validation header (defaults to false)"""
    mock_new_connection.return_value = MagicMock()

    request = Mock(spec=Request)
    request.headers = {
        "EDGE_URL": "https://edge.local:8443",
        "EDGE_API_CLIENT_ID": "client-id",
        "EDGE_API_CLIENT_SECRET": "secret",
        # VALIDATE_CERTIFICATE not provided
    }

    _ = get_litmus_connection(request)

    # Verify default is False (header defaults to "false")
    call_kwargs = mock_new_connection.call_args[1]
    assert call_kwargs["validate_certificate"] is False


# ==================== Test: Missing Headers ====================


def test_authentication_missing_edge_url():
    """Test error when EDGE_URL header is missing"""
    request = Mock(spec=Request)
    request.headers = {
        # Missing EDGE_URL
        "EDGE_API_CLIENT_ID": "client-id",
        "EDGE_API_CLIENT_SECRET": "secret",
    }

    with pytest.raises(McpError) as exc_info:
        get_litmus_connection(request)

    assert "EDGE_URL" in str(exc_info.value)
    assert "required" in str(exc_info.value).lower()


def test_authentication_missing_client_id():
    """Test error when CLIENT_ID header is missing"""
    request = Mock(spec=Request)
    request.headers = {
        "EDGE_URL": "https://edge.local:8443",
        # Missing CLIENT_ID
        "EDGE_API_CLIENT_SECRET": "secret",
    }

    with pytest.raises(McpError) as exc_info:
        get_litmus_connection(request)

    assert "EDGE_API_CLIENT_ID" in str(exc_info.value)
    assert "required" in str(exc_info.value).lower()


def test_authentication_missing_client_secret():
    """Test error when CLIENT_SECRET header is missing"""
    request = Mock(spec=Request)
    request.headers = {
        "EDGE_URL": "https://edge.local:8443",
        "EDGE_API_CLIENT_ID": "client-id",
        # Missing CLIENT_SECRET
    }

    with pytest.raises(McpError) as exc_info:
        get_litmus_connection(request)

    assert "EDGE_API_CLIENT_SECRET" in str(exc_info.value)
    assert "required" in str(exc_info.value).lower()


# ==================== Test: Connection Errors ====================


@patch("utils.auth.new_le_connection")
def test_authentication_connection_failure(mock_new_connection, valid_edge_headers):
    """Test error handling when connection creation fails"""
    mock_new_connection.side_effect = ConnectionError("Unable to connect to Edge")

    request = Mock(spec=Request)
    request.headers = valid_edge_headers

    with pytest.raises(McpError) as exc_info:
        get_litmus_connection(request)

    assert "Failed to connect to Litmus Edge" in str(exc_info.value)


# ==================== Test: Edge Cases ====================


@patch("utils.auth.new_le_connection")
def test_authentication_case_sensitivity(mock_new_connection):
    """Test that header keys are case-sensitive"""
    request = Mock(spec=Request)
    request.headers = {
        "edge_url": "https://edge.local:8443",  # Wrong case
        "edge_api_client_id": "client-id",  # Wrong case
        "edge_api_client_secret": "secret",  # Wrong case
    }

    # Should fail because headers are case-sensitive
    with pytest.raises(McpError):
        get_litmus_connection(request)


@patch("utils.auth.new_le_connection")
def test_authentication_certificate_validation_case_insensitive(mock_new_connection):
    """Test that certificate validation value is case-insensitive"""
    mock_new_connection.return_value = MagicMock()

    # Test various cases
    for cert_value in ["TRUE", "True", "tRuE", "FALSE", "False", "fAlSe"]:
        request = Mock(spec=Request)
        request.headers = {
            "EDGE_URL": "https://edge.local:8443",
            "EDGE_API_CLIENT_ID": "client-id",
            "EDGE_API_CLIENT_SECRET": "secret",
            "VALIDATE_CERTIFICATE": cert_value,
        }

        _ = get_litmus_connection(request)

        call_kwargs = mock_new_connection.call_args[1]
        expected = cert_value.lower() == "true"
        assert call_kwargs["validate_certificate"] == expected


# ==================== Test: Stateless Behavior ====================


@patch("utils.auth.new_le_connection")
def test_authentication_creates_new_connection_per_request(mock_new_connection):
    """Test that each request creates a new isolated connection"""
    mock_connection1 = MagicMock()
    mock_connection2 = MagicMock()
    mock_new_connection.side_effect = [mock_connection1, mock_connection2]

    request1 = Mock(spec=Request)
    request1.headers = {
        "EDGE_URL": "https://edge1.local:8443",
        "EDGE_API_CLIENT_ID": "client1",
        "EDGE_API_CLIENT_SECRET": "secret1",
    }

    request2 = Mock(spec=Request)
    request2.headers = {
        "EDGE_URL": "https://edge2.local:8443",
        "EDGE_API_CLIENT_ID": "client2",
        "EDGE_API_CLIENT_SECRET": "secret2",
    }

    # Execute two requests
    result1 = get_litmus_connection(request1)
    result2 = get_litmus_connection(request2)

    # Verify different connections were created
    assert result1 != result2
    assert mock_new_connection.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
