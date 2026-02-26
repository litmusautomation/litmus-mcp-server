"""
Tests for InfluxDB query input validation (Fix B)

Covers the measurement name and time_range sanitisation added to
get_historical_data_from_influxdb_tool before the query is built via
f-string interpolation.

Key cases:
  - Valid / invalid measurement names (regex: [w][w-.]+)
  - Valid / invalid time_range values  (regex: d+(ms|[usmhdw]))
  - Realistic injection payloads are rejected
  - Error messages are descriptive enough for callers to self-correct
"""

import asyncio
import json
import os
import sys
import pytest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp.shared.exceptions import McpError
from tools.data_tools import get_historical_data_from_influxdb_tool


# ── helpers ────────────────────────────────────────────────────────────────


def _make_request():
    """Minimal mock request — get_influx_connection_params is always patched."""
    return Mock()


def _influx_params():
    return {
        "INFLUX_HOST": "10.0.0.1",
        "INFLUX_PORT": 8086,
        "INFLUX_USERNAME": "admin",
        "INFLUX_PASSWORD": "password",
        "INFLUX_DB_NAME": "tsdata",
    }


def _run(coro):
    return asyncio.run(coro)


# ── measurement name — valid ───────────────────────────────────────────────


@pytest.mark.parametrize("measurement", [
    "temperature",       # plain word
    "cpu_usage",         # underscore
    "my-measurement",    # hyphen
    "device.temperature", # dot
    "_internal",         # underscore start
])
def test_valid_measurement_names_reach_influxdb(measurement):
    """Valid names should pass validation and reach the InfluxDB client."""
    args = {"measurement": measurement, "time_range": "1h"}

    with patch("tools.data_tools.get_influx_connection_params", return_value=_influx_params()):
        with patch("tools.data_tools.influxdb.InfluxDBClient") as mock_client:
            mock_client.return_value.query.return_value.get_points.return_value = []

            result = _run(get_historical_data_from_influxdb_tool(_make_request(), args))

    response = json.loads(result[0].text)
    assert response["success"] is True
    # Confirm the query was actually executed (not blocked before it)
    mock_client.return_value.query.assert_called_once()


# ── measurement name — invalid ─────────────────────────────────────────────


@pytest.mark.parametrize("measurement", [
    '"; DROP DATABASE tsdata; --',   # SQL-style injection
    "spaces not allowed",            # whitespace
    "semi;colon",                    # semicolon
    " leading_space",                # leading space
])
def test_invalid_measurement_names_raise_mcp_error(measurement):
    """Dangerous/invalid measurement names must be rejected before query build."""
    args = {"measurement": measurement, "time_range": "1h"}

    with patch("tools.data_tools.get_influx_connection_params", return_value=_influx_params()):
        with pytest.raises(McpError):
            _run(get_historical_data_from_influxdb_tool(_make_request(), args))


def test_invalid_measurement_name_never_reaches_influxdb():
    """An invalid measurement must not result in any InfluxDB call."""
    args = {"measurement": '"; DROP DATABASE tsdata; --', "time_range": "1h"}

    with patch("tools.data_tools.get_influx_connection_params", return_value=_influx_params()):
        with patch("tools.data_tools.influxdb.InfluxDBClient") as mock_client:
            with pytest.raises(McpError):
                _run(get_historical_data_from_influxdb_tool(_make_request(), args))

    mock_client.assert_not_called()


def test_invalid_measurement_error_message_is_descriptive():
    """The McpError message should mention 'measurement' so the caller can fix it."""
    args = {"measurement": "bad name!", "time_range": "1h"}

    with patch("tools.data_tools.get_influx_connection_params", return_value=_influx_params()):
        with pytest.raises(McpError) as exc_info:
            _run(get_historical_data_from_influxdb_tool(_make_request(), args))

    assert "measurement" in str(exc_info.value).lower()


# ── time_range — valid ─────────────────────────────────────────────────────


@pytest.mark.parametrize("time_range", [
    "1h",    # hours
    "30m",   # minutes
    "7d",    # days
    "500ms", # milliseconds
    "1u",    # microseconds
])
def test_valid_time_ranges_reach_influxdb(time_range):
    """Valid InfluxDB duration strings should pass validation."""
    args = {"measurement": "temperature", "time_range": time_range}

    with patch("tools.data_tools.get_influx_connection_params", return_value=_influx_params()):
        with patch("tools.data_tools.influxdb.InfluxDBClient") as mock_client:
            mock_client.return_value.query.return_value.get_points.return_value = []

            result = _run(get_historical_data_from_influxdb_tool(_make_request(), args))

    response = json.loads(result[0].text)
    assert response["success"] is True
    mock_client.return_value.query.assert_called_once()


# ── time_range — invalid ───────────────────────────────────────────────────


@pytest.mark.parametrize("time_range", [
    "1h; DROP SERIES /.*/",   # injection attempt
    "1hour",                   # verbose word, not InfluxDB format
    "1H",                      # uppercase unit
    "1 h",                     # space in duration
    "-1h",                     # negative
])
def test_invalid_time_ranges_raise_mcp_error(time_range):
    """Invalid duration strings must be rejected before query build."""
    args = {"measurement": "temperature", "time_range": time_range}

    with patch("tools.data_tools.get_influx_connection_params", return_value=_influx_params()):
        with pytest.raises(McpError):
            _run(get_historical_data_from_influxdb_tool(_make_request(), args))


def test_invalid_time_range_never_reaches_influxdb():
    """An invalid time_range must not result in any InfluxDB call."""
    args = {"measurement": "temperature", "time_range": "1h; DROP SERIES /.*/"}

    with patch("tools.data_tools.get_influx_connection_params", return_value=_influx_params()):
        with patch("tools.data_tools.influxdb.InfluxDBClient") as mock_client:
            with pytest.raises(McpError):
                _run(get_historical_data_from_influxdb_tool(_make_request(), args))

    mock_client.assert_not_called()


def test_invalid_time_range_error_message_includes_example():
    """The McpError message should include a valid example like '1h' or '30m'."""
    args = {"measurement": "temperature", "time_range": "bad_value"}

    with patch("tools.data_tools.get_influx_connection_params", return_value=_influx_params()):
        with pytest.raises(McpError) as exc_info:
            _run(get_historical_data_from_influxdb_tool(_make_request(), args))

    error_text = str(exc_info.value)
    assert "1h" in error_text or "30m" in error_text or "7d" in error_text


# ── combined injection scenarios ───────────────────────────────────────────


def test_both_fields_invalid_measurement_checked_first():
    """When both fields are invalid, measurement is validated first."""
    args = {
        "measurement": "bad name!",
        "time_range": "not_a_duration",
    }

    with patch("tools.data_tools.get_influx_connection_params", return_value=_influx_params()):
        with pytest.raises(McpError) as exc_info:
            _run(get_historical_data_from_influxdb_tool(_make_request(), args))

    assert "measurement" in str(exc_info.value).lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
