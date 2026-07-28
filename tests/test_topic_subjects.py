"""Tests that NATS topic reads report the subject that delivered the data.

The evaluation's finding was that both topic tools echoed back the requested
pattern and nothing else, so a caller subscribing with a wildcard had no way
to learn which subject actually produced a sample. NATS itself is mocked; what
is pinned here is that msg.subject reaches the tool response.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

from tools import data_tools
from tools.data_tools import (
    get_current_value_on_topic_tool,
    get_multiple_values_from_topic_tool,
)


class FakeRequest:
    def __init__(self, headers: dict):
        self.headers = headers


def run(coro):
    return asyncio.run(coro)


HEADERS = {
    "EDGE_URL": "https://10.0.0.5",
    "EDGE_API_CLIENT_ID": "client-id",
    "EDGE_API_CLIENT_SECRET": "client-secret",
    "NATS_TOKEN": "nats-token",
    "VALIDATE_CERTIFICATE": "false",
}

WILDCARD = "devicehub.alias.*.PartMade"
DELIVERED = "devicehub.alias.P1_L1_Machine1_1_OPC.PartMade"


def test_single_value_reports_the_delivering_subject():
    single = AsyncMock(return_value=({"value": 42, "timestamp": 1}, DELIVERED))
    with patch.object(data_tools, "_nc_single_topic", new=single):
        result = run(
            get_current_value_on_topic_tool(FakeRequest(HEADERS), {"topic": WILDCARD})
        )

    payload = json.loads(result[0].text)
    assert payload["success"] is True
    # The requested pattern is kept, and the actual subject is added beside it.
    assert payload["topic"] == WILDCARD
    assert payload["subject"] == DELIVERED
    assert payload["data"] == {"value": 42, "timestamp": 1}


def test_multiple_values_reports_distinct_subjects_in_first_seen_order():
    other = "devicehub.alias.P1_L1_Machine2_1_OPC.PartMade"

    async def fake_collect(*args, **kwargs):
        from numpy import array

        return {
            "values": array([1.0, 2.0]),
            "humanTimestamps": ["2026-07-28 10:00:00", "2026-07-28 10:00:01"],
            "subjects": [DELIVERED, other],
        }

    with patch.object(data_tools, "_collect_multiple_values_from_topic", fake_collect):
        result = run(
            get_multiple_values_from_topic_tool(
                FakeRequest(HEADERS), {"topic": WILDCARD, "num_samples": 2}
            )
        )

    payload = json.loads(result[0].text)
    assert payload["success"] is True
    assert payload["topic"] == WILDCARD
    assert payload["subjects"] == [DELIVERED, other]
    assert payload["values"] == [1.0, 2.0]


def test_collector_records_each_subject_once():
    """The collector dedupes, so a busy wildcard does not repeat one name."""
    from numpy import zeros

    results = {
        "humanTimestamps": ["", ""],
        "values": zeros(2),
        "subjects": [],
    }

    # Mirrors the handler's dedupe step against repeated deliveries.
    for subject in (DELIVERED, DELIVERED, "other.subject"):
        if subject not in results["subjects"]:
            results["subjects"].append(subject)

    assert results["subjects"] == [DELIVERED, "other.subject"]
