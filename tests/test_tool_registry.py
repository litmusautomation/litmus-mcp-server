"""Wave 2 registry invariants: shape, categories, uniqueness, alias integrity."""

import sys
from pathlib import Path

# Make `src/` importable the same way other tests do.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp.types import ToolAnnotations  # noqa: E402

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
    "sdk.fallback",
}

EXPECTED_CANONICAL_COUNT = 60


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


def test_every_tool_has_title_and_hint_annotations():
    """Anthropic connectors directory requirement: every tool must carry a
    title and either readOnlyHint=True or destructiveHint=True."""
    for tool in ALL_TOOLS:
        ann = tool.get("annotations")
        assert isinstance(
            ann, ToolAnnotations
        ), f"{tool['name']}: missing ToolAnnotations"
        assert (
            isinstance(ann.title, str) and ann.title
        ), f"{tool['name']}: annotations.title is required"
        if ann.readOnlyHint is True:
            assert (
                ann.destructiveHint is not True
            ), f"{tool['name']}: read-only tool must not be destructive"
        else:
            assert ann.readOnlyHint is False and ann.destructiveHint is True, (
                f"{tool['name']}: write tool needs readOnlyHint=False and "
                "destructiveHint=True"
            )


def test_tool_names_within_directory_length_limit():
    """Directory review rejects tool names longer than 64 characters."""
    for tool in ALL_TOOLS:
        assert len(tool["name"]) <= 64, f"{tool['name']}: name exceeds 64 chars"


def test_get_device_logs_alias_removed():
    """The deprecated get_device_logs alias was removed in favor of
    get_system_events and must not be reintroduced."""
    assert "get_device_logs" not in TOOL_BY_NAME
    assert "get_system_events" in TOOL_BY_NAME
