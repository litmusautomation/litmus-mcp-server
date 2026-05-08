"""Wave 2 registry invariants: shape, categories, uniqueness, alias integrity."""

import sys
from pathlib import Path

# Make `src/` importable the same way other tests do.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from server import ALL_TOOLS, TOOL_BY_NAME  # noqa: E402

ALLOWED_CATEGORIES = {
    "devicehub.drivers",
    "devicehub.devices",
    "devicehub.tags",
    "system.identity",
    "system.cloud",
    "system.events",
    "system.network",
    "system.pcap",
    "marketplace.containers",
    "nats.topics",
    "datahub.influx",
    "datahub.queries",
    "digitaltwins.models",
    "digitaltwins.instances",
    "digitaltwins.attributes",
    "digitaltwins.hierarchy",
    "lem.fleet",
    "lem.licensing",
    "lem.dashboard",
    "lem.companies",
    "lem.tenant",
    "lem.bridge",
}

EXPECTED_CANONICAL_COUNT = 57


def test_every_entry_well_formed():
    for tool in ALL_TOOLS:
        assert isinstance(tool.get("name"), str) and tool["name"], tool
        assert isinstance(tool.get("description"), str) and tool["description"], tool[
            "name"
        ]
        assert isinstance(tool.get("schema"), dict), tool["name"]
        assert callable(tool.get("handler")), tool["name"]
        assert isinstance(tool.get("category"), str) and tool["category"], tool["name"]


def test_categories_in_allowlist():
    for tool in ALL_TOOLS:
        assert (
            tool["category"] in ALLOWED_CATEGORIES
        ), f"{tool['name']}: unknown category {tool['category']!r}"


def test_no_duplicate_names():
    names = [tool["name"] for tool in ALL_TOOLS]
    assert len(names) == len(set(names)), "duplicate tool names detected"


def test_canonical_count_matches_expected():
    canonical = [tool for tool in ALL_TOOLS if not tool.get("deprecated")]
    assert (
        len(canonical) == EXPECTED_CANONICAL_COUNT
    ), f"expected {EXPECTED_CANONICAL_COUNT} canonical tools, got {len(canonical)}"


def test_aliases_share_canonical_handler():
    canonical_handlers = {
        tool["handler"] for tool in ALL_TOOLS if not tool.get("deprecated")
    }
    for tool in ALL_TOOLS:
        if tool.get("deprecated"):
            assert (
                tool["handler"] in canonical_handlers
            ), f"deprecated alias {tool['name']!r} points to handler with no canonical entry"


def test_tool_by_name_covers_every_entry():
    assert len(TOOL_BY_NAME) == len(ALL_TOOLS)
    for tool in ALL_TOOLS:
        assert TOOL_BY_NAME[tool["name"]] is tool


def test_get_device_logs_alias_points_to_get_system_events_handler():
    """get_device_logs is the deprecation alias for get_system_events."""
    canonical = TOOL_BY_NAME["get_system_events"]
    alias = TOOL_BY_NAME["get_device_logs"]
    assert alias.get("deprecated") is True
    assert alias["handler"] is canonical["handler"]
    assert "DEPRECATED" in alias["description"]
