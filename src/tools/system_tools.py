import time
from config import logger
from utils.auth import get_litmus_connection
from utils.formatting import format_success_response, format_error_response

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, TextContent, ToolAnnotations
from starlette.requests import Request
from litmussdk.system import events as sys_events, network as sys_network


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

        events_list = (
            result.get("events", result) if isinstance(result, dict) else result
        )
        severity_list = result.get("severityList") if isinstance(result, dict) else None

        return format_success_response(
            {
                "from_timestamp": from_ts,
                "to_timestamp": to_ts,
                "component": component,
                "severity_filter": severity,
                "count": len(events_list) if isinstance(events_list, list) else None,
                "severity_list": severity_list,
                "events": events_list,
            }
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting device logs: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_system_event_stats(
    request: Request, arguments: dict
) -> list[TextContent]:
    try:
        connection = get_litmus_connection(request)
        result = sys_events.event_management_stats(le_connection=connection)
        return format_success_response(
            result if isinstance(result, dict) else {"stats": result}
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting event stats: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


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
            "Returns event manager statistics as reported by the LE API. "
            "NOTE: current LE versions report only the event store size in "
            "bytes ({\"size\": <int>}); richer metrics appear only when the "
            "edge provides them. Use get_system_events to read actual event "
            "messages."
        ),
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": get_system_event_stats,
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
