"""
Tests for NATS/InfluxDB host fallback to EDGE_URL.

When NATS_SOURCE / INFLUX_HOST are not configured, the connection param
resolvers derive the host from EDGE_URL (scheme, port, and path stripped)
and flag it with derived_from_edge_url so tools can surface a note to the
caller. Explicit values always win, and when neither is present the error
tells the caller which of the two settings is needed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp.shared.exceptions import McpError

from utils.auth import (
    _data_plane_host,
    get_nats_connection_params,
    get_influx_connection_params,
)
from tools.data_tools import (
    _get_connect_options,
    _nats_connection_note,
    _influx_connection_note,
)


class _Headers:
    def __init__(self, values):
        self._values = {k.lower(): v for k, v in values.items()}

    def get(self, key, default=None):
        return self._values.get(key.lower(), default)


class _FakeRequest:
    def __init__(self, headers):
        self.headers = _Headers(headers)


# ── _data_plane_host ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://edge.example.com", "edge.example.com"),
        ("https://edge.example.com:8443", "edge.example.com"),
        ("https://edge.example.com/", "edge.example.com"),
        ("https://edge.example.com:8443/some/path", "edge.example.com"),
        ("https://10.0.0.5", "10.0.0.5"),
        ("10.0.0.5", "10.0.0.5"),
        ("10.0.0.5:443", "10.0.0.5"),
        ("  https://edge.example.com  ", "edge.example.com"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_data_plane_host(raw, expected):
    assert _data_plane_host(raw) == expected


# ── NATS param resolution ───────────────────────────────────────────────────


def test_nats_host_derived_from_edge_url():
    request = _FakeRequest({"EDGE_URL": "https://edge.example.com:8443"})
    params = get_nats_connection_params(request)
    assert params["nats_source"] == "edge.example.com"
    assert params["nats_port"] == "4222"
    assert params["derived_from_edge_url"] is True


def test_explicit_nats_source_wins_over_edge_url():
    request = _FakeRequest(
        {
            "EDGE_URL": "https://edge.example.com",
            "NATS_SOURCE": "10.0.0.9",
            "NATS_PORT": "14222",
        }
    )
    params = get_nats_connection_params(request)
    assert params["nats_source"] == "10.0.0.9"
    assert params["nats_port"] == "14222"
    assert params["derived_from_edge_url"] is False


def test_explicit_nats_source_is_sanitized():
    request = _FakeRequest({"NATS_SOURCE": "https://10.0.0.9:8443/"})
    params = get_nats_connection_params(request)
    assert params["nats_source"] == "10.0.0.9"
    assert params["derived_from_edge_url"] is False


def test_nats_missing_both_raises_either_or_error():
    request = _FakeRequest({})
    with pytest.raises(McpError) as exc_info:
        get_nats_connection_params(request)
    message = str(exc_info.value)
    assert "EDGE_URL" in message
    assert "NATS_SOURCE" in message


def test_nats_credentials_passed_through():
    request = _FakeRequest(
        {
            "EDGE_URL": "https://edge.example.com",
            "NATS_USER": "user1",
            "NATS_PASSWORD": "pass1",
            "NATS_TOKEN": "tok1",
            "NATS_TLS": "false",
        }
    )
    params = get_nats_connection_params(request)
    assert params["nats_user"] == "user1"
    assert params["nats_password"] == "pass1"
    assert params["nats_token"] == "tok1"
    assert params["use_tls"] is False


# ── Influx param resolution ─────────────────────────────────────────────────


def _influx_creds():
    return {"INFLUX_USERNAME": "admin", "INFLUX_PASSWORD": "secret"}


def test_influx_host_derived_from_edge_url():
    request = _FakeRequest(
        {"EDGE_URL": "https://edge.example.com:8443", **_influx_creds()}
    )
    params = get_influx_connection_params(request)
    assert params["INFLUX_HOST"] == "edge.example.com"
    assert params["INFLUX_PORT"] == 8086
    assert params["INFLUX_DB_NAME"] == "tsdata"
    assert params["derived_from_edge_url"] is True


def test_explicit_influx_host_wins_over_edge_url():
    request = _FakeRequest(
        {
            "EDGE_URL": "https://edge.example.com",
            "INFLUX_HOST": "10.0.0.9",
            "INFLUX_PORT": "18086",
            **_influx_creds(),
        }
    )
    params = get_influx_connection_params(request)
    assert params["INFLUX_HOST"] == "10.0.0.9"
    assert params["INFLUX_PORT"] == 18086
    assert params["derived_from_edge_url"] is False


def test_explicit_influx_host_is_sanitized():
    request = _FakeRequest({"INFLUX_HOST": "https://10.0.0.9/", **_influx_creds()})
    params = get_influx_connection_params(request)
    assert params["INFLUX_HOST"] == "10.0.0.9"


def test_influx_missing_both_raises_either_or_error():
    request = _FakeRequest(_influx_creds())
    with pytest.raises(McpError) as exc_info:
        get_influx_connection_params(request)
    message = str(exc_info.value)
    assert "EDGE_URL" in message
    assert "INFLUX_HOST" in message


def test_influx_username_still_required():
    request = _FakeRequest(
        {"EDGE_URL": "https://edge.example.com", "INFLUX_PASSWORD": "secret"}
    )
    with pytest.raises(McpError) as exc_info:
        get_influx_connection_params(request)
    assert "INFLUX_USERNAME" in str(exc_info.value)


# ── connect options ─────────────────────────────────────────────────────────


def test_connect_options_prefer_token_over_user_password():
    opts = _get_connect_options(
        "edge.example.com", "4222", "user1", "pass1", use_tls=False, nats_token="tok1"
    )
    assert opts["servers"] == ["nats://edge.example.com:4222"]
    assert opts["token"] == "tok1"
    assert "user" not in opts

    opts = _get_connect_options("edge.example.com", "4222", "user1", "pass1", False)
    assert opts["user"] == "user1"
    assert opts["password"] == "pass1"
    assert "token" not in opts


# ── connection notes ────────────────────────────────────────────────────────


def test_nats_note_only_when_derived():
    derived = {
        "nats_source": "edge.example.com",
        "nats_port": "4222",
        "derived_from_edge_url": True,
    }
    note = _nats_connection_note(derived)
    assert "nats://edge.example.com:4222" in note
    assert "EDGE_URL" in note

    explicit = {**derived, "derived_from_edge_url": False}
    assert _nats_connection_note(explicit) is None


def test_influx_note_only_when_derived():
    derived = {
        "INFLUX_HOST": "edge.example.com",
        "INFLUX_PORT": 8086,
        "derived_from_edge_url": True,
    }
    note = _influx_connection_note(derived)
    assert "http://edge.example.com:8086" in note
    assert "EDGE_URL" in note

    explicit = {**derived, "derived_from_edge_url": False}
    assert _influx_connection_note(explicit) is None
