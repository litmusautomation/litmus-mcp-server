import time
from config import logger
from utils.auth import get_litmus_connection
from utils.formatting import format_success_response, format_error_response

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, TextContent
from starlette.requests import Request
from litmussdk.system import events as sys_events, network as sys_network


async def get_device_logs(request: Request, arguments: dict) -> list[TextContent]:
    try:
        now = int(time.time())
        from_ts = int(arguments.get("from_timestamp", now - 3600))
        to_ts = int(arguments.get("to_timestamp", now))
        component = arguments.get("component")
        severity = arguments.get("severity")
        limit = min(int(arguments.get("limit", 100)), 1000)

        if severity and severity.upper() not in ("INFO", "WARN", "ALERT", "ERROR"):
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Invalid severity '{severity}'. Must be INFO, WARN, ALERT, or ERROR",
            ))

        connection = get_litmus_connection(request)
        result = sys_events.get_events(
            from_timestamp=from_ts,
            to_timestamp=to_ts,
            component=component,
            count=limit,
            severity=severity.upper() if severity else None,
            le_connection=connection,
        )

        events_list = result.get("events", result) if isinstance(result, dict) else result
        severity_list = result.get("severityList") if isinstance(result, dict) else None

        return format_success_response({
            "from_timestamp": from_ts,
            "to_timestamp": to_ts,
            "component": component,
            "severity_filter": severity,
            "count": len(events_list) if isinstance(events_list, list) else None,
            "severity_list": severity_list,
            "events": events_list,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting device logs: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_system_event_stats(request: Request, arguments: dict) -> list[TextContent]:
    try:
        connection = get_litmus_connection(request)
        result = sys_events.event_management_stats(le_connection=connection)
        return format_success_response(result if isinstance(result, dict) else {"stats": result})

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting event stats: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_firewall_rules(request: Request, arguments: dict) -> list[TextContent]:
    try:
        connection = get_litmus_connection(request)
        rules = sys_network.firewall_rules(le_connection=connection)
        return format_success_response({
            "count": len(rules),
            "rules": rules,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting firewall rules: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_network_interface_info(request: Request, arguments: dict) -> list[TextContent]:
    try:
        interface = arguments.get("interface", "eth0")
        connection = get_litmus_connection(request)
        details = sys_network.network_interface_details(
            network_name=interface, le_connection=connection
        )
        return format_success_response({
            "interface": interface,
            "details": details,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting network interface info: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_packet_capture_interfaces(request: Request, arguments: dict) -> list[TextContent]:
    try:
        connection = get_litmus_connection(request)
        result = sys_network.get_packet_capture_interfaces(le_connection=connection)
        return format_success_response(result if isinstance(result, dict) else {"interfaces": result})

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting packet capture interfaces: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_packet_capture_status(request: Request, arguments: dict) -> list[TextContent]:
    try:
        connection = get_litmus_connection(request)
        result = sys_network.packet_capture_status(le_connection=connection)
        return format_success_response(result if isinstance(result, dict) else {"status": result})

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
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message="'duration' must be between 1 and 30 minutes",
            ))

        connection = get_litmus_connection(request)
        sys_network.start_stop_packet_capture(
            action="start",
            interface=interface,
            duration=str(duration),
            le_connection=connection,
        )
        return format_success_response({
            "action": "started",
            "interface": interface,
            "duration_minutes": duration,
        })

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
