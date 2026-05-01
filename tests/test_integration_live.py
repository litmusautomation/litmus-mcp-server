"""
Live integration tests against a real Litmus Edge.

These tests hit a real Edge and require credentials. They auto-skip if
EDGE_URL / EDGE_API_CLIENT_ID / EDGE_API_CLIENT_SECRET aren't set in
the environment (loaded from .env if present).

Devices, drivers, register names, and value types are discovered at runtime —
no fixture data is hardcoded, so the same suite is reproducible against any
Edge that has at least one configured device.

Run with:
    uv run pytest tests/test_integration_live.py -v
"""

import asyncio
import json
import os
import sys
import warnings

import pytest
from dotenv import load_dotenv

try:
    import urllib3

    urllib3.disable_warnings()
except ImportError:
    pass

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


EDGE_URL = _env("EDGE_URL")
CLIENT_ID = _env("EDGE_API_CLIENT_ID")
CLIENT_SECRET = _env("EDGE_API_CLIENT_SECRET")
INFLUX_HOST = _env("INFLUX_HOST", "")
INFLUX_PORT = _env("INFLUX_PORT", "8086")
INFLUX_DB = _env("INFLUX_DB_NAME", "tsdata")
INFLUX_USER = _env("INFLUX_USERNAME", "")
INFLUX_PASS = _env("INFLUX_PASSWORD", "")

requires_edge = pytest.mark.skipif(
    not (EDGE_URL and CLIENT_ID and CLIENT_SECRET),
    reason="Live Edge credentials not configured (set EDGE_URL/EDGE_API_CLIENT_ID/EDGE_API_CLIENT_SECRET).",
)
requires_influx = pytest.mark.skipif(
    not (INFLUX_HOST and INFLUX_USER and INFLUX_PASS),
    reason="InfluxDB credentials not configured.",
)


class _Headers(dict):
    """dict subclass so request.headers.get(key, default) works like Starlette."""


def _make_request():
    from unittest.mock import MagicMock

    req = MagicMock()
    req.headers = _Headers(
        {
            "EDGE_URL": EDGE_URL,
            "EDGE_API_CLIENT_ID": CLIENT_ID,
            "EDGE_API_CLIENT_SECRET": CLIENT_SECRET,
            "INFLUX_HOST": INFLUX_HOST,
            "INFLUX_PORT": INFLUX_PORT,
            "INFLUX_DB_NAME": INFLUX_DB,
            "INFLUX_USERNAME": INFLUX_USER,
            "INFLUX_PASSWORD": INFLUX_PASS,
            "VALIDATE_CERTIFICATE": "false",
        }
    )
    return req


@pytest.fixture(scope="module")
def request_obj():
    return _make_request()


@pytest.fixture(scope="module")
def le_connection():
    from litmussdk.utils.conn import new_le_connection

    return new_le_connection(
        edge_url=EDGE_URL,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        validate_certificate=False,
    )


@pytest.fixture(scope="module")
def all_devices(le_connection):
    from litmussdk.devicehub import devices as dh_devices

    return dh_devices.list_devices(le_connection=le_connection)


@pytest.fixture(scope="module")
def device_with_tag(le_connection, all_devices):
    """First device that has at least one configured tag with an output topic."""
    from litmussdk.devicehub import tags as dh_tags

    for d in all_devices:
        try:
            tag_list = dh_tags.list_registers_from_single_device(
                d, le_connection=le_connection
            )
        except Exception:
            continue
        for t in tag_list:
            for tp in t.topics or []:
                if tp.direction == "Output":
                    return {"device": d, "tag": t, "topic": tp.topic}
    pytest.skip("No device with a configured tag found on this Edge.")


@pytest.fixture(scope="module")
def crud_target(le_connection, all_devices):
    """
    Find a device + register pair suitable for create/update/delete.
    A 'suitable' register has default values for every required property
    so we don't need driver-specific knowledge to build the payload.
    """
    for d in all_devices:
        try:
            for sr in d.driver.supported_registers or []:
                # Need at least one valid valueType
                value_types = []
                missing_required = []
                for prop in sr.properties or []:
                    if prop.name == "valueType" and prop.list_values:
                        value_types = [lv.value for lv in prop.list_values]
                    elif prop.required and prop.default_value is None:
                        missing_required.append(prop.name)
                if not value_types or missing_required:
                    continue
                return {
                    "device": d,
                    "register_name": sr.name,
                    "value_type": value_types[0],
                }
        except Exception:
            continue
    pytest.skip("No device with a fully-defaulted register found on this Edge.")


# ── Tests ─────────────────────────────────────────────────────────────────────


@requires_edge
@requires_influx
def test_query_tag_data(request_obj, device_with_tag):
    from tools.data_tools import query_tag_data

    result = asyncio.run(
        query_tag_data(
            request_obj,
            {
                "device_name": device_with_tag["device"].name,
                "tag_name": device_with_tag["tag"].tag_name,
                "time_range": "24h",
                "limit": 5,
            },
        )
    )
    data = json.loads(result[0].text)
    assert data.get("success") is True, data
    assert data["measurement"] == device_with_tag["topic"]
    assert "count" in data
    assert isinstance(data["data"], list)


@requires_edge
@requires_influx
def test_get_tag_statistics(request_obj, device_with_tag):
    from tools.data_tools import get_tag_statistics

    result = asyncio.run(
        get_tag_statistics(
            request_obj,
            {
                "device_name": device_with_tag["device"].name,
                "tag_name": device_with_tag["tag"].tag_name,
                "time_range": "24h",
            },
        )
    )
    data = json.loads(result[0].text)
    assert data.get("success") is True, data
    assert data["measurement"] == device_with_tag["topic"]
    assert "statistics" in data


@requires_edge
def test_tag_crud_cycle(request_obj, crud_target):
    """create_devicehub_tag → update_devicehub_tag → delete_devicehub_tag."""
    from tools.devicehub_tools import (
        create_devicehub_tag,
        update_devicehub_tag,
        delete_devicehub_tag,
    )

    device_name = crud_target["device"].name
    tag_name = "mcp_integration_test_tag"

    # Create
    result = asyncio.run(
        create_devicehub_tag(
            request_obj,
            {
                "device_name": device_name,
                "tag_name": tag_name,
                "register_name": crud_target["register_name"],
                "value_type": crud_target["value_type"],
            },
        )
    )
    data = json.loads(result[0].text)
    assert data.get("success") is True, f"create failed: {data}"
    tag_id = data["tag_id"]
    assert tag_id

    try:
        # Update
        result = asyncio.run(
            update_devicehub_tag(
                request_obj,
                {
                    "device_name": device_name,
                    "tag_name": tag_name,
                    "description": "updated by integration test",
                },
            )
        )
        data = json.loads(result[0].text)
        assert data.get("success") is True, f"update failed: {data}"
        assert data["tag_id"] == tag_id
    finally:
        # Delete (best-effort cleanup even if update fails)
        result = asyncio.run(
            delete_devicehub_tag(
                request_obj,
                {
                    "device_name": device_name,
                    "tag_name": tag_name,
                },
            )
        )
        data = json.loads(result[0].text)
        assert data.get("success") is True, f"delete failed: {data}"
