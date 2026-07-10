"""Tests for the generic SDK fallback tools backed by litmus-cli.

The CLI subprocess layer (_run_cli) is mocked throughout; these tests pin the
approval gate, the header-to-env forwarding contract, and error handling.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from mcp.shared.exceptions import McpError

from tools.sdk_cli_tools import (
    _build_cli_env,
    _resolve_cli_binary,
    call_litmus_sdk_function,
    discover_litmus_sdk_functions,
)


class FakeRequest:
    def __init__(self, headers: dict):
        self.headers = headers  # plain dict provides .get()


def run(coro):
    return asyncio.run(coro)


EDGE_HEADERS = {
    "EDGE_URL": "https://10.0.0.5",
    "EDGE_API_CLIENT_ID": "client-id",
    "EDGE_API_CLIENT_SECRET": "client-secret",
    "VALIDATE_CERTIFICATE": "false",
}

BRIDGE_HEADERS = {
    "EDGE_MANAGER_URL": "https://lem.example.com",
    "EDGE_API_TOKEN": "token",
    "EDGE_MANAGER_PROJECT_ID": "proj-1",
    "EDGE_MANAGER_DEVICE_ID": "dev-1",
}


# ---------------------------------------------------------------- env


def test_env_forwards_edge_headers_verbatim():
    env = _build_cli_env(FakeRequest(EDGE_HEADERS))
    for key, value in EDGE_HEADERS.items():
        assert env[key] == value


def test_env_is_isolated_from_process_environ():
    env = _build_cli_env(FakeRequest(EDGE_HEADERS))
    assert "PATH" not in env
    assert "HOME" not in env
    assert env["LITMUS_CONFIG_DIR"]
    assert env["LITMUS_DEVICEHUB_CACHE_DIR"]


def test_env_sets_lem_bridge_when_all_bridge_headers_present():
    env = _build_cli_env(FakeRequest(BRIDGE_HEADERS))
    assert env["USE_LEM_BRIDGE"] == "true"


def test_env_respects_explicit_use_lem_bridge_header():
    headers = {**BRIDGE_HEADERS, "USE_LEM_BRIDGE": "false"}
    env = _build_cli_env(FakeRequest(headers))
    assert env["USE_LEM_BRIDGE"] == "false"


def test_env_omits_bridge_flag_when_bridge_headers_incomplete():
    headers = {k: v for k, v in BRIDGE_HEADERS.items() if k != "EDGE_MANAGER_DEVICE_ID"}
    env = _build_cli_env(FakeRequest(headers))
    assert "USE_LEM_BRIDGE" not in env


# ---------------------------------------------------------------- approval gate


def test_call_rejected_without_user_approval():
    with pytest.raises(McpError, match="explicit user approval"):
        run(
            call_litmus_sdk_function(
                FakeRequest(EDGE_HEADERS), {"function": "le.devicehub.ListDevices"}
            )
        )


def test_call_rejected_when_user_approved_false():
    with pytest.raises(McpError, match="explicit user approval"):
        run(
            call_litmus_sdk_function(
                FakeRequest(EDGE_HEADERS),
                {"function": "le.devicehub.ListDevices", "user_approved": False},
            )
        )


def test_call_rejected_without_function():
    with pytest.raises(McpError, match="'function' parameter is required"):
        run(call_litmus_sdk_function(FakeRequest(EDGE_HEADERS), {"user_approved": True}))


def test_call_rejected_when_args_not_object():
    with pytest.raises(McpError, match="'args' must be a JSON object"):
        run(
            call_litmus_sdk_function(
                FakeRequest(EDGE_HEADERS),
                {
                    "function": "le.devicehub.ListDevices",
                    "user_approved": True,
                    "args": "not-a-dict",
                },
            )
        )


# ---------------------------------------------------------------- call


def test_approved_call_invokes_run_with_json_args():
    mock = AsyncMock(return_value=(0, '{"devices": []}', ""))
    with patch("tools.sdk_cli_tools._run_cli", mock):
        result = run(
            call_litmus_sdk_function(
                FakeRequest(EDGE_HEADERS),
                {
                    "function": "le.devicehub.ListDevices",
                    "args": {"limit": 5},
                    "user_approved": True,
                },
            )
        )
    argv = mock.call_args.args[0]
    assert argv[:2] == ["run", "le.devicehub.ListDevices"]
    assert argv[2] == "--args"
    assert json.loads(argv[3]) == {"limit": 5}
    payload = json.loads(result[0].text)
    assert payload["success"] is True
    assert payload["result"] == {"devices": []}


def test_approved_call_without_args_omits_args_flag():
    mock = AsyncMock(return_value=(0, "{}", ""))
    with patch("tools.sdk_cli_tools._run_cli", mock):
        run(
            call_litmus_sdk_function(
                FakeRequest(EDGE_HEADERS),
                {"function": "le.system.Version", "user_approved": True},
            )
        )
    assert mock.call_args.args[0] == ["run", "le.system.Version"]


def test_call_nonzero_exit_returns_error_response():
    mock = AsyncMock(return_value=(1, "", "Error: unknown function"))
    with patch("tools.sdk_cli_tools._run_cli", mock):
        result = run(
            call_litmus_sdk_function(
                FakeRequest(EDGE_HEADERS),
                {"function": "nope.Nope", "user_approved": True},
            )
        )
    payload = json.loads(result[0].text)
    assert payload["success"] is False
    assert payload["error"] == "sdk_call_failed"
    assert "unknown function" in payload["message"]


# ---------------------------------------------------------------- discover


def test_discover_lists_with_prefix():
    mock = AsyncMock(return_value=(0, "le.devicehub.ListDevices()\n", ""))
    with patch("tools.sdk_cli_tools._run_cli", mock):
        result = run(
            discover_litmus_sdk_functions(
                FakeRequest(EDGE_HEADERS), {"prefix": "le.devicehub"}
            )
        )
    assert mock.call_args.args[0] == ["list", "le.devicehub"]
    payload = json.loads(result[0].text)
    assert payload["success"] is True
    assert "le.devicehub.ListDevices" in payload["functions"]


def test_discover_without_prefix_lists_all():
    mock = AsyncMock(return_value=(0, "le.analytics.GetTopics()\n", ""))
    with patch("tools.sdk_cli_tools._run_cli", mock):
        run(discover_litmus_sdk_functions(FakeRequest(EDGE_HEADERS), {}))
    assert mock.call_args.args[0] == ["list"]


# ---------------------------------------------------------------- binary resolution


def test_missing_binary_raises_with_install_hint(monkeypatch):
    monkeypatch.delenv("LITMUS_CLI_PATH", raising=False)
    with patch("tools.sdk_cli_tools.shutil.which", return_value=None):
        with pytest.raises(McpError, match="litmus-sdk-releases"):
            _resolve_cli_binary()


def test_old_binary_name_used_as_fallback(monkeypatch):
    monkeypatch.delenv("LITMUS_CLI_PATH", raising=False)
    which = {"litmus-cli": None, "litmus-sdk-cli": "/usr/local/bin/litmus-sdk-cli"}
    with patch("tools.sdk_cli_tools.shutil.which", side_effect=which.get):
        assert _resolve_cli_binary() == "/usr/local/bin/litmus-sdk-cli"


def test_bad_explicit_path_raises(monkeypatch):
    monkeypatch.setenv("LITMUS_CLI_PATH", "/nonexistent/litmus-cli")
    with pytest.raises(McpError, match="not an executable file"):
        _resolve_cli_binary()
