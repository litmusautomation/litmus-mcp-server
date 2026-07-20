"""Regression tests for the 2026-07 tool evaluation findings: secret
redaction, hierarchy save round-trip, InfluxDB measurement resolution, and
the device-creation connection path."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from starlette.requests import Request

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp.shared.exceptions import McpError  # noqa: E402

from utils.formatting import redact_secrets, REDACTED  # noqa: E402
from tools.digitaltwins_tools import _to_save_hierarchy  # noqa: E402
from tools.data_tools import (  # noqa: E402
    _device_measurement_name,
    _tag_data_source,
)


def _run(coro):
    return asyncio.run(coro)


def _parse(result):
    return json.loads(result[0].text)


def _make_request(headers=None):
    request = Mock(spec=Request)
    request.headers = headers or {
        "EDGE_URL": "https://test-edge.local",
        "EDGE_API_CLIENT_ID": "id",
        "EDGE_API_CLIENT_SECRET": "secret",
    }
    return request


# ── secret redaction ─────────────────────────────────────────────────────────


def test_redacts_secret_keys_and_pem_blocks():
    data = {
        "name": "Milo",
        "properties": {
            "Password": "hunter2",
            "SessionPrivateKey": "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
            "mqttPassword": "mq-secret",
            "registryPassword": "reg-secret",
            "activationCode": "ABC-123",
            "Port": 4840,
        },
    }
    out = redact_secrets(data)
    props = out["properties"]
    assert props["Password"] == REDACTED
    assert props["SessionPrivateKey"] == REDACTED
    assert props["mqttPassword"] == REDACTED
    assert props["registryPassword"] == REDACTED
    assert props["activationCode"] == REDACTED
    assert props["Port"] == 4840
    assert out["name"] == "Milo"


def test_redacts_pem_private_key_under_any_key():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----"
    assert redact_secrets({"innocuousField": pem})["innocuousField"] == REDACTED


def test_redacts_name_value_property_entries():
    entries = [
        {"Name": "Password", "Value": "hunter2"},
        {"Name": "Address", "Value": "40001"},
    ]
    out = redact_secrets(entries)
    assert out[0]["Value"] == REDACTED
    assert out[1]["Value"] == "40001"


def test_does_not_redact_flags_or_metadata_about_secrets():
    data = {
        "DisableEncryptedPasswordCheck": "false",
        "tokenExpiry": "2026-08-01T00:00:00Z",
        "passwordUpdated": "2026-01-01",
        "ValidateCertificate": True,
    }
    out = redact_secrets(data)
    assert out == data


# ── hierarchy save transform ─────────────────────────────────────────────────

GETTER_HIERARCHY = {
    "Name": "root",
    "Node": None,
    "Attr": None,
    "Childs": [
        {
            "Name": "Properties",
            "Node": {
                "ID": "7090d019-6f67-44d0-b5bd-e710b3107638",
                "ModelID": "145f6e30-73d7-4dbe-90df-fc21a57ea810",
                "Position": 0,
                "ParentID": None,
                "Name": "Properties",
                "IsFolder": True,
                "AttributeID": "00000000-0000-0000-0000-000000000000",
                "AttributeType": None,
                "NodeType": "folder",
            },
            "Attr": None,
            "Childs": [
                {
                    "Name": "ModelId",
                    "Node": {
                        "ID": "523b27ab-becc-4814-8473-a05e28994915",
                        "ModelID": "145f6e30-73d7-4dbe-90df-fc21a57ea810",
                        "Position": 0,
                        "ParentID": "7090d019-6f67-44d0-b5bd-e710b3107638",
                        "Name": "ModelId",
                        "IsFolder": False,
                        "AttributeID": "09e4b787-7561-4e42-aae8-86e91bd5ec75",
                        "AttributeType": "static",
                        "NodeType": "attribute",
                    },
                    "Attr": {"ID": "09e4b787", "Key": "ModelId", "Value": "x"},
                    "Childs": [],
                }
            ],
        }
    ],
}


def test_hierarchy_transform_strips_rejected_fields():
    nodes = _to_save_hierarchy(GETTER_HIERARCHY)
    assert len(nodes) == 1
    top = nodes[0]
    # only Node and Childs keys survive at each level
    assert set(top.keys()) == {"Node", "Childs"}
    assert set(top["Node"].keys()) <= {
        "Position",
        "Name",
        "IsFolder",
        "AttributeID",
        "AttributeType",
        "NodeType",
    }
    assert top["Node"]["Name"] == "Properties"
    child = top["Childs"][0]
    assert child["Node"]["AttributeType"] == "static"
    assert "ID" not in child["Node"]
    assert "Attr" not in child


def test_hierarchy_transform_accepts_node_list_and_single_node():
    as_list = _to_save_hierarchy(GETTER_HIERARCHY["Childs"])
    as_single = _to_save_hierarchy(GETTER_HIERARCHY["Childs"][0])
    assert as_list == _to_save_hierarchy(GETTER_HIERARCHY)
    assert as_single == as_list


def test_hierarchy_transform_rejects_scalars():
    with pytest.raises(McpError):
        _to_save_hierarchy("not-a-hierarchy")


# ── InfluxDB measurement resolution ──────────────────────────────────────────


def _device(name="Gen", device_id="22B596E7-1890-44CC-AE7B-671D5264DD56"):
    d = MagicMock()
    d.name = name
    d.id = device_id
    return d


def _tag(tag_id="T-1", topic="devicehub.alias.Gen.testdouble"):
    t = MagicMock()
    t.id = tag_id
    tp = MagicMock()
    tp.direction = "Output"
    tp.topic = topic
    t.topics = [tp]
    return t


def test_device_measurement_resolved_by_name_dot_id():
    names = ["avg", "solar", "Gen.22B596E7-1890-44CC-AE7B-671D5264DD56"]
    assert (
        _device_measurement_name(names, _device())
        == "Gen.22B596E7-1890-44CC-AE7B-671D5264DD56"
    )


def test_tag_source_prefers_legacy_topic_measurement():
    names = ["devicehub.alias.Gen.testdouble"]
    measurement, where = _tag_data_source(names, _device(), _tag())
    assert measurement == "devicehub.alias.Gen.testdouble"
    assert where == ""


def test_tag_source_falls_back_to_device_measurement_with_register_filter():
    names = ["Gen.22B596E7-1890-44CC-AE7B-671D5264DD56"]
    measurement, where = _tag_data_source(names, _device(), _tag())
    assert measurement == "Gen.22B596E7-1890-44CC-AE7B-671D5264DD56"
    assert where == "\"register_id\" = 'T-1' AND "


def test_tag_source_final_fallback_is_raw_topic():
    measurement, where = _tag_data_source([], _device(), _tag())
    assert measurement == "devicehub.alias.Gen.testdouble"
    assert where == ""


# ── create_devicehub_device connection path ──────────────────────────────────


@patch("tools.devicehub_tools.devices.create_device")
@patch("tools.devicehub_tools.devices.Device.model_validate")
@patch("tools.devicehub_tools.list_all_drivers")
@patch("tools.devicehub_tools.get_litmus_connection")
def test_create_device_passes_driver_object_and_connection_context(
    mock_connection, mock_list_drivers, mock_validate, mock_create
):
    """Regression: passing the driver id string made the Device model resolve
    it through the SDK's env-based default connection (the placeholder-host
    failure); the Driver object plus explicit context avoids that."""
    from tools.devicehub_tools import create_devicehub_device

    connection = MagicMock()
    mock_connection.return_value = connection
    driver = MagicMock()
    driver.name = "Generator"
    driver.get_default_properties.return_value = {"pollingInterval": "1000"}
    mock_list_drivers.return_value = [driver]

    created = MagicMock()
    created.id = "NEW-ID"
    created.name = "dev-1"
    created.description = ""
    mock_validate.return_value = MagicMock()
    mock_create.return_value = created

    result = _run(
        create_devicehub_device(
            _make_request(), {"name": "dev-1", "selected_driver": "Generator"}
        )
    )
    data = _parse(result)

    assert data["success"] is True
    assert data["device"] == {
        "id": "NEW-ID",
        "name": "dev-1",
        "driver": "Generator",
        "description": "",
    }
    payload = mock_validate.call_args.args[0]
    assert payload["driver"] is driver
    assert mock_validate.call_args.kwargs["context"] == {
        "le_connection": connection
    }
    mock_create.assert_called_once()
    assert mock_create.call_args.kwargs["le_connection"] is connection
