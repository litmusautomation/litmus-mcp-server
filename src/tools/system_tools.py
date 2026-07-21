import platform
import time
from importlib import metadata as importlib_metadata

from config import logger, server_version
from utils.auth import get_litmus_connection
from utils.formatting import format_success_response, format_error_response

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, TextContent, ToolAnnotations
from starlette.requests import Request
from litmussdk.system import (
    events as sys_events,
    network as sys_network,
    general as sys_general,
)


async def get_system_events_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    try:
        now = int(time.time())
        from_ts = int(arguments.get("from_timestamp", now - 3600))
        to_ts = int(arguments.get("to_timestamp", now))
        component = arguments.get("component")
        severity = arguments.get("severity")
        limit = min(int(arguments.get("limit", 100)), 1000)

        if severity and severity.upper() not in ("INFO", "WARN", "ALERT", "ERROR"):
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Invalid severity '{severity}'. Must be INFO, WARN, ALERT, or ERROR",
                )
            )

        connection = get_litmus_connection(request)
        result = sys_events.get_events(
            from_timestamp=from_ts,
            to_timestamp=to_ts,
            component=component,
            count=limit,
            severity=severity.upper() if severity else None,
            le_connection=connection,
        )

        # Payload shape: {"severityList": [...], "events": {"events": [...],
        # "total": N}} - unwrap both levels so callers get a flat list.
        payload = result.get("events", result) if isinstance(result, dict) else result
        if isinstance(payload, dict):
            events_list = payload.get("events") or []
            total = payload.get("total", len(events_list))
        else:
            events_list = payload or []
            total = len(events_list) if isinstance(events_list, list) else None
        severity_list = result.get("severityList") if isinstance(result, dict) else None

        return format_success_response(
            {
                "from_timestamp": from_ts,
                "to_timestamp": to_ts,
                "component": component,
                "severity_filter": severity,
                "count": len(events_list) if isinstance(events_list, list) else None,
                "total_in_range": total,
                "severity_list": severity_list,
                "events": events_list,
            }
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting system events: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_system_event_stats(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Event store stats enriched with recent-event severity counts and
    system health (memory/storage/CPU). Each block is independent: a failing
    endpoint becomes an *_error note instead of failing the whole call."""
    try:
        connection = get_litmus_connection(request)
        result: dict = {}

        try:
            stats = sys_events.event_management_stats(le_connection=connection)
            result["event_store"] = (
                stats if isinstance(stats, dict) else {"stats": stats}
            )
        except Exception as e:
            result["event_store_error"] = str(e)

        try:
            now = int(time.time())
            events = sys_events.get_events(
                from_timestamp=now - 3600,
                to_timestamp=now,
                component=None,
                count=1000,
                severity=None,
                le_connection=connection,
            )
            # Payload shape: {"severityList": [...], "events": {"events":
            # [...], "total": N}} - unwrap both levels.
            payload = events.get("events") if isinstance(events, dict) else events
            if isinstance(payload, dict):
                events_list = payload.get("events") or []
                total = payload.get("total", len(events_list))
            else:
                events_list = payload or []
                total = len(events_list)
            by_severity: dict = {}
            for ev in events_list:
                if not isinstance(ev, dict):
                    continue
                sev = (ev.get("severity") or "UNKNOWN").upper()
                by_severity[sev] = by_severity.get(sev, 0) + 1
            result["recent_events_1h"] = {
                "total": total,
                "by_severity": by_severity,
            }
        except Exception as e:
            result["recent_events_error"] = str(e)

        health: dict = {}
        try:
            memory = sys_general.memory_info(le_connection=connection)
            result["memory"] = memory
            if memory.get("memTotal"):
                health["memory_used_pct"] = round(
                    100 * memory.get("memUsed", 0) / memory["memTotal"], 1
                )
        except Exception as e:
            result["memory_error"] = str(e)
        try:
            storage = sys_general.storage_info(le_connection=connection)
            result["storage"] = storage
            if storage.get("dataSize"):
                used = storage["dataSize"] - storage.get("dataFree", 0)
                health["data_storage_used_pct"] = round(
                    100 * used / storage["dataSize"], 1
                )
        except Exception as e:
            result["storage_error"] = str(e)
        try:
            cpus = sys_general.cpu_info(le_connection=connection)
            result["cpu_count"] = len(cpus) if isinstance(cpus, list) else None
        except Exception as e:
            result["cpu_error"] = str(e)
        if health:
            result["health"] = health

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting event stats: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


_MCP_TAGS_URL = (
    "https://api.github.com/repos/litmusautomation/litmus-mcp-server"
    "/tags?per_page=30"
)


async def get_mcp_server_info(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Version and environment info about the MCP server itself, with an
    optional update check against GitHub and an optional litmus-cli upgrade.
    The base info requires no edge connection, so it always works."""
    import asyncio as _asyncio
    import json as _json

    from tools.sdk_cli_tools import (
        _resolve_cli_binary,
        _run_cli,
        _build_cli_env,
        _fetch,
        _pinned_cli_version,
        get_latest_cli_tag,
        upgrade_cli_binary,
        version_key,
    )

    try:
        arguments = arguments or {}
        upgrade_cli = bool(arguments.get("upgrade_cli"))
        check_updates = bool(arguments.get("check_updates")) or upgrade_cli

        info: dict = {
            "mcp_server_version": server_version(),
            "python_version": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}",
        }
        try:
            info["litmussdk_version"] = importlib_metadata.version("litmussdk")
        except Exception:
            info["litmussdk_version"] = None

        async def _read_cli_version():
            try:
                binary = _resolve_cli_binary()
            except McpError:
                return None, None
            returncode, stdout, _ = await _run_cli(
                ["--version"], _build_cli_env(request)
            )
            lines = (stdout or "").strip().splitlines()
            return binary, (lines[0] if returncode == 0 and lines else None)

        binary, cli_version = await _read_cli_version()
        info["litmus_cli_path"] = binary
        info["litmus_cli_version"] = cli_version
        info["litmus_cli_pinned_release"] = _pinned_cli_version()
        if binary is None:
            info["litmus_cli_note"] = (
                "litmus-cli not installed; it is downloaded automatically on "
                "first use of a CLI-backed tool"
            )

        if check_updates:
            updates: dict = {}
            try:
                latest_cli = await _asyncio.to_thread(get_latest_cli_tag)
                updates["litmus_cli_latest_release"] = latest_cli
                if latest_cli and cli_version and version_key(cli_version):
                    updates["litmus_cli_update_available"] = version_key(
                        latest_cli
                    ) > version_key(cli_version)
                else:
                    updates["litmus_cli_update_available"] = None
            except Exception as e:
                updates["litmus_cli_check_error"] = str(e)

            try:
                tags = _json.loads(await _asyncio.to_thread(_fetch, _MCP_TAGS_URL))
                names = [
                    t.get("name")
                    for t in tags
                    if isinstance(t, dict) and version_key(t.get("name", ""))
                ]
                latest_mcp = max(names, key=version_key) if names else None
                updates["mcp_server_latest_release"] = latest_mcp
                current = info["mcp_server_version"]
                if latest_mcp and current:
                    available = version_key(latest_mcp) > version_key(current)
                    updates["mcp_server_update_available"] = available
                    if available:
                        updates["mcp_server_update_note"] = (
                            "A newer MCP server release exists. Update by "
                            "pulling the latest release from "
                            "https://github.com/litmusautomation/litmus-mcp-server "
                            "(or rebuilding the Docker image) and restarting; "
                            "the server cannot upgrade itself in place."
                        )
                else:
                    updates["mcp_server_update_available"] = None
            except Exception as e:
                updates["mcp_server_check_error"] = str(e)

            info["updates"] = updates

        if upgrade_cli:
            try:
                tag, path = await upgrade_cli_binary()
                _, new_version = await _read_cli_version()
                info["litmus_cli_upgrade"] = {
                    "upgraded_to": tag,
                    "path": path,
                    "active_version": new_version,
                    "note": (
                        "Upgrade is active for this server process; a restart "
                        "reverts to the configured pin unless LITMUS_CLI_PATH "
                        "is updated."
                    ),
                }
                info["litmus_cli_path"] = path
                info["litmus_cli_version"] = new_version
            except Exception as e:
                info["litmus_cli_upgrade_error"] = str(e)

        return format_success_response(info)

    except Exception as e:
        logger.error(f"Error getting MCP server info: {e}", exc_info=True)
        return format_error_response("info_failed", str(e))


async def get_firewall_rules(request: Request, arguments: dict) -> list[TextContent]:
    try:
        connection = get_litmus_connection(request)
        rules = sys_network.firewall_rules(le_connection=connection)
        return format_success_response(
            {
                "count": len(rules),
                "rules": rules,
            }
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting firewall rules: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_network_interface_info(
    request: Request, arguments: dict
) -> list[TextContent]:
    try:
        interface = arguments.get("interface", "eth0")
        connection = get_litmus_connection(request)
        details = sys_network.network_interface_details(
            network_name=interface, le_connection=connection
        )
        return format_success_response(
            {
                "interface": interface,
                "details": details,
            }
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting network interface info: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_packet_capture_interfaces(
    request: Request, arguments: dict
) -> list[TextContent]:
    try:
        connection = get_litmus_connection(request)
        result = sys_network.get_packet_capture_interfaces(le_connection=connection)
        return format_success_response(
            result if isinstance(result, dict) else {"interfaces": result}
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting packet capture interfaces: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_packet_capture_status(
    request: Request, arguments: dict
) -> list[TextContent]:
    try:
        connection = get_litmus_connection(request)
        result = sys_network.packet_capture_status(le_connection=connection)
        return format_success_response(
            result if isinstance(result, dict) else {"status": result}
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting packet capture status: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def start_packet_capture(request: Request, arguments: dict) -> list[TextContent]:
    try:
        interface = arguments.get("interface", "eth0")
        duration = int(arguments.get("duration", 1))

        if not 1 <= duration <= 30:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="'duration' must be between 1 and 30 minutes",
                )
            )

        connection = get_litmus_connection(request)
        sys_network.start_stop_packet_capture(
            action="start",
            interface=interface,
            duration=str(duration),
            le_connection=connection,
        )
        return format_success_response(
            {
                "action": "started",
                "interface": interface,
                "duration_minutes": duration,
            }
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error starting packet capture: {e}", exc_info=True)
        return format_error_response("capture_failed", str(e))


async def stop_packet_capture(request: Request, arguments: dict) -> list[TextContent]:
    try:
        connection = get_litmus_connection(request)
        sys_network.start_stop_packet_capture(action="stop", le_connection=connection)
        return format_success_response({"action": "stopped"})

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error stopping packet capture: {e}", exc_info=True)
        return format_error_response("capture_failed", str(e))


_GET_SYSTEM_EVENTS_DESC = (
    "Retrieves system events and logs from Litmus Edge. "
    "Filter by time range, component, and severity. "
    "Use get_system_event_stats for event store statistics instead."
)

_GET_SYSTEM_EVENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "from_timestamp": {
            "type": "integer",
            "description": "Start time as Unix epoch seconds (default: 1 hour ago)",
        },
        "to_timestamp": {
            "type": "integer",
            "description": "End time as Unix epoch seconds (default: now)",
        },
        "component": {
            "type": "string",
            "description": "Filter by component name (optional)",
        },
        "severity": {
            "type": "string",
            "description": "Filter by severity: INFO, WARN, ALERT, or ERROR (optional)",
        },
        "limit": {
            "type": "integer",
            "description": "Max events to return (default 100, max 1000)",
            "default": 100,
        },
    },
    "required": [],
}


TOOLS = [
    {
        "name": "get_system_events",
        "category": "system.events",
        "annotations": ToolAnnotations(title="Get System Events", readOnlyHint=True),
        "description": _GET_SYSTEM_EVENTS_DESC,
        "schema": _GET_SYSTEM_EVENTS_SCHEMA,
        "handler": get_system_events_tool,
    },
    {
        "name": "get_system_event_stats",
        "category": "system.events",
        "annotations": ToolAnnotations(title="Get System Event Stats", readOnlyHint=True),
        "description": (
            "System and event health snapshot: event store size, last-hour "
            "event counts by severity, memory/storage usage with percentages, "
            "and CPU count. Use this to check overall edge health. "
            "Use get_system_events to read actual event messages."
        ),
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": get_system_event_stats,
    },
    {
        "name": "get_mcp_server_info",
        "category": "server.info",
        # readOnlyHint=True despite upgrade_cli: the only mutation is the
        # server's own CLI cache (same as the automatic bootstrap that
        # read-only CLI-backed tools already perform); no user or edge data
        # is touched, and a restart reverts to the pin.
        "annotations": ToolAnnotations(title="Get MCP Server Info", readOnlyHint=True),
        "description": (
            "Returns version information about this MCP server itself: server "
            "version, litmussdk version, litmus-cli version/path/pin, Python "
            "and platform. Needs no edge connection - always available. Use "
            "when the user asks what version they are running or when "
            "gathering support/triage information. Pass check_updates=true to "
            "also compare against the latest GitHub releases (requires "
            "internet on the server host) and report update availability. "
            "Pass upgrade_cli=true to download and switch to the newest "
            "litmus-cli release (checksum-verified, affects only this server "
            "process; the MCP server itself cannot self-upgrade and only "
            "reports how to update)."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "check_updates": {
                    "type": "boolean",
                    "description": "Also check GitHub for newer MCP server and litmus-cli releases (default false)",
                    "default": False,
                },
                "upgrade_cli": {
                    "type": "boolean",
                    "description": "Download and activate the newest litmus-cli release for this server process (implies check_updates)",
                    "default": False,
                },
            },
            "required": [],
        },
        "handler": get_mcp_server_info,
    },
    {
        "name": "get_firewall_rules",
        "category": "system.network",
        "annotations": ToolAnnotations(title="Get Firewall Rules", readOnlyHint=True),
        "description": (
            "Returns the firewall rules configured on this Litmus Edge device: "
            "ports, protocols, and ALLOW/DENY actions. "
            "Use this to diagnose network connectivity or security configuration."
        ),
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": get_firewall_rules,
    },
    {
        "name": "get_network_interface_info",
        "category": "system.network",
        "annotations": ToolAnnotations(title="Get Network Interface Info", readOnlyHint=True),
        "description": (
            "Returns network interface details for the Litmus Edge device: "
            "IP address, MAC, gateway, link status, MTU, and speed. "
            "Defaults to eth0. Use this to check network configuration."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "interface": {
                    "type": "string",
                    "description": "Interface name (default 'eth0')",
                    "default": "eth0",
                },
            },
            "required": [],
        },
        "handler": get_network_interface_info,
    },
    {
        "name": "get_packet_capture_interfaces",
        "category": "system.pcap",
        "annotations": ToolAnnotations(title="List Packet Capture Interfaces", readOnlyHint=True),
        "description": (
            "Lists network interfaces available for packet capture on Litmus Edge "
            "(e.g. eth0, wlan0). Use this before starting a capture to pick the right interface."
        ),
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": get_packet_capture_interfaces,
    },
    {
        "name": "get_packet_capture_status",
        "category": "system.pcap",
        "annotations": ToolAnnotations(title="Get Packet Capture Status", readOnlyHint=True),
        "description": (
            "Returns the current packet capture state and list of captured .pcap files with metadata. "
            "Use start_packet_capture / stop_packet_capture to control capture."
        ),
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": get_packet_capture_status,
    },
    {
        "name": "start_packet_capture",
        "category": "system.pcap",
        "annotations": ToolAnnotations(title="Start Packet Capture", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Starts a packet capture on a Litmus Edge network interface. "
            "Duration is 1-30 minutes. Let it run to completion - the pcap file "
            "is only retained when the capture finishes naturally. Use "
            "get_packet_capture_status to check progress."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "interface": {
                    "type": "string",
                    "description": "Interface to capture on (default 'eth0')",
                    "default": "eth0",
                },
                "duration": {
                    "type": "integer",
                    "description": "Capture duration in minutes (1-30, default 1)",
                    "default": 1,
                },
            },
            "required": [],
        },
        "handler": start_packet_capture,
    },
    {
        "name": "stop_packet_capture",
        "category": "system.pcap",
        "annotations": ToolAnnotations(title="Stop Packet Capture", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Stops an in-progress packet capture on Litmus Edge. "
            "WARNING: stopping early discards the pcap file - only use this to abort "
            "a capture you don't want. To keep the pcap, let start_packet_capture run "
            "to completion instead. "
            "Use get_packet_capture_status to confirm state before and after."
        ),
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": stop_packet_capture,
    },
]
