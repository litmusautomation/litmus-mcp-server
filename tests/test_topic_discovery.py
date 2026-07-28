"""Tests for list_nats_topics, the cross-component topic listing.

Both topic-reading tools need an exact subject and nothing listed what
existed, so callers had to know subjects out of band. No single Litmus API
returns them all, so this merges the three components that publish topics.
The CLI layer is mocked; what is pinned here is the merge, the per-source
status reporting, and that one unavailable component never hides the others.
"""

import asyncio
import json
from unittest.mock import patch

import pytest
from mcp.shared.exceptions import McpError

from tools import data_tools
from tools.data_tools import list_nats_topics


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def run(coro):
    return asyncio.run(coro)


def parse(result):
    return json.loads(result[0].text)


ANALYTICS = [
    {"Topic": "devicehub.alias.M1.PartMade", "Format": "json", "Direction": "Output"},
    {"Topic": "analytics.only.topic", "Format": "json", "Direction": "Output"},
]

TAG_PAGE = {
    "TotalCount": 2,
    "Last": True,
    "Registers": [
        {
            "TagName": "PartMade",
            "Topics": [
                {
                    "Topic": "devicehub.alias.M1.PartMade",
                    "Direction": "Output",
                    "Format": "json",
                    "IntervalMs": 1000,
                }
            ],
        },
        {
            "TagName": "Temp",
            "Topics": [{"Topic": "devicehub.alias.M1.Temp", "Direction": "Output"}],
        },
    ],
}

INSTANCES = [
    {"Name": "Machine1Twin", "Topic": "digitaltwins.Machine1Twin"},
    {"Name": "NoTopic"},  # instances without a topic are skipped
]


def _cli(analytics=None, tags=None, instances=None, fail=()):
    """Stub run_cli_function dispatching on the dotted path."""

    async def fake(request, function, args):
        if function in fail:
            raise RuntimeError(f"{function} unavailable")
        if function == "le.analytics.GetTopics":
            return ANALYTICS if analytics is None else analytics
        if function == "le.devicehub.ListAllTags":
            return TAG_PAGE if tags is None else tags
        if function == "le.digitaltwins.ListAllInstances":
            return INSTANCES if instances is None else instances
        raise AssertionError(f"unexpected function {function}")

    return patch.object(data_tools, "run_cli_function", fake)


def test_merges_all_three_components_and_dedupes_by_topic():
    with _cli():
        payload = parse(run(list_nats_topics(FakeRequest(), {})))

    assert payload["success"] is True
    topics = {t["topic"]: t for t in payload["topics"]}
    assert set(topics) == {
        "devicehub.alias.M1.PartMade",
        "devicehub.alias.M1.Temp",
        "analytics.only.topic",
        "digitaltwins.Machine1Twin",
    }
    # A subject both components know about is reported once, naming both.
    shared = topics["devicehub.alias.M1.PartMade"]
    assert sorted(shared["sources"]) == ["analytics", "devicehub"]
    # Component-specific metadata survives the merge.
    assert shared["interval_ms"] == 1000
    assert topics["devicehub.alias.M1.Temp"]["owner"] == "Temp"
    assert topics["digitaltwins.Machine1Twin"]["owner"] == "Machine1Twin"
    assert payload["total_count"] == 4
    assert payload["has_more"] is False


def test_results_are_sorted_by_topic():
    with _cli():
        payload = parse(run(list_nats_topics(FakeRequest(), {})))
    names = [t["topic"] for t in payload["topics"]]
    assert names == sorted(names)


def test_sources_can_be_restricted():
    with _cli():
        payload = parse(
            run(list_nats_topics(FakeRequest(), {"sources": ["digitaltwins"]}))
        )
    assert [t["topic"] for t in payload["topics"]] == ["digitaltwins.Machine1Twin"]
    assert set(payload["sources"]) == {"digitaltwins"}


def test_unknown_source_is_rejected():
    with _cli(), pytest.raises(McpError, match="Unknown source"):
        run(list_nats_topics(FakeRequest(), {"sources": ["nope"]}))


def test_pattern_filters_case_insensitively():
    with _cli():
        payload = parse(run(list_nats_topics(FakeRequest(), {"pattern": "PARTMADE"})))
    assert [t["topic"] for t in payload["topics"]] == ["devicehub.alias.M1.PartMade"]
    assert payload["total_count"] == 1


def test_pagination_reports_next_offset():
    with _cli():
        first = parse(run(list_nats_topics(FakeRequest(), {"limit": 2})))
    assert len(first["topics"]) == 2
    assert first["total_count"] == 4
    assert first["has_more"] is True
    assert first["next_offset"] == 2

    with _cli():
        second = parse(
            run(list_nats_topics(FakeRequest(), {"limit": 2, "offset": 2}))
        )
    assert second["has_more"] is False
    assert not {t["topic"] for t in first["topics"]} & {
        t["topic"] for t in second["topics"]
    }


def test_one_unavailable_component_does_not_hide_the_others():
    """Analytics GetTopics needs LE 4.0.x+, so older firmware must degrade."""
    with _cli(fail=("le.analytics.GetTopics",)):
        payload = parse(run(list_nats_topics(FakeRequest(), {})))

    assert payload["success"] is True
    assert payload["sources"]["analytics"]["status"] == "unavailable"
    assert "unavailable" in payload["sources"]["analytics"]["reason"]
    assert payload["sources"]["devicehub"]["status"] == "ok"
    # analytics.only.topic is gone, but everything else survives.
    names = {t["topic"] for t in payload["topics"]}
    assert "analytics.only.topic" not in names
    assert "devicehub.alias.M1.Temp" in names


def test_all_sources_failing_is_an_error_not_an_empty_list():
    failures = (
        "le.analytics.GetTopics",
        "le.devicehub.ListAllTags",
        "le.digitaltwins.ListAllInstances",
    )
    with _cli(fail=failures):
        payload = parse(run(list_nats_topics(FakeRequest(), {})))

    assert payload["success"] is False
    assert payload["error"] == "topic_discovery_failed"


def test_truncated_tag_scan_says_so_rather_than_looking_complete():
    """A silent cap would read as a full listing of a fleet's topics."""
    page = {
        "TotalCount": 50_000,
        "Last": False,
        "Registers": [
            {"TagName": f"T{i}", "Topics": [{"Topic": f"dh.T{i}"}]}
            for i in range(data_tools._TAG_SCAN_PAGE)
        ],
    }
    with _cli(analytics=[], tags=page, instances=[]):
        payload = parse(run(list_nats_topics(FakeRequest(), {"limit": 1})))

    devicehub = payload["sources"]["devicehub"]
    assert devicehub["status"] == "partial"
    assert devicehub["tags_total"] == 50_000
    assert devicehub["tags_scanned"] == data_tools._MAX_TAG_SCAN
    assert str(data_tools._MAX_TAG_SCAN) in devicehub["note"]
