from starlette.requests import Request
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, TextContent

from litmussdk.lem.lifecycle.edgedevs.general import (
    get_devices_paginated,
    get_current_device_details,
    get_device_versions,
    get_device_tags,
    get_license_expiry_in_x_days,
    get_expired_licenses,
)
from litmussdk.lem.lifecycle.dashboard import (
    dashboard_usage,
    get_project_alerts,
)

from utils.auth import get_lem_connection, get_lem_project_id
from utils.formatting import format_success_response, format_error_response
from config import logger


async def lem_list_devices_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """List edge devices in a LEM project (paginated)."""
    try:
        connection = get_lem_connection(request)
        project_id = get_lem_project_id(request, arguments)
        args = arguments or {}
        page = int(args.get("page", 0))
        limit = int(args.get("limit", 10))
        status = args.get("status", "ACTIVE")

        result = get_devices_paginated(
            project_id=project_id,
            status=status,
            page=page,
            limit=limit,
            raw=True,
            connection=connection,
        )
        logger.info(f"LEM list_devices project={project_id} page={page} limit={limit}")
        return format_success_response({"page": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM list_devices failed: {e}", exc_info=True)
        return format_error_response("lem_list_devices_failed", str(e))


async def lem_get_device_details_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Get full details for a single edge device by its LEM device id."""
    try:
        connection = get_lem_connection(request)
        project_id = get_lem_project_id(request, arguments)
        device_id = (arguments or {}).get("device_id")
        if not device_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'device_id' parameter is required"
                )
            )

        result = get_current_device_details(
            project_id=project_id,
            device_id=device_id,
            raw=True,
            connection=connection,
        )
        logger.info(f"LEM device_details project={project_id} device={device_id}")
        return format_success_response({"device": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM device_details failed: {e}", exc_info=True)
        return format_error_response("lem_device_details_failed", str(e))


async def lem_list_device_versions_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """List Litmus Edge versions registered in a LEM project."""
    try:
        connection = get_lem_connection(request)
        project_id = get_lem_project_id(request, arguments)
        result = get_device_versions(
            project_id=project_id, raw=True, connection=connection
        )
        logger.info(f"LEM device_versions project={project_id}")
        return format_success_response({"versions": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM device_versions failed: {e}", exc_info=True)
        return format_error_response("lem_device_versions_failed", str(e))


async def lem_list_device_groups_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """List device group/tag labels defined in a LEM project."""
    try:
        connection = get_lem_connection(request)
        project_id = get_lem_project_id(request, arguments)
        result = get_device_tags(project_id=project_id, connection=connection)
        logger.info(f"LEM device_groups project={project_id}")
        return format_success_response({"groups": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM device_groups failed: {e}", exc_info=True)
        return format_error_response("lem_device_groups_failed", str(e))


async def lem_get_license_expiry_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """List devices in a LEM project whose license expires within N days."""
    try:
        connection = get_lem_connection(request)
        project_id = get_lem_project_id(request, arguments)
        expiry_days = (arguments or {}).get("expiry_days")
        if expiry_days is None:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'expiry_days' parameter is required"
                )
            )
        result = get_license_expiry_in_x_days(
            project_id=project_id,
            expiry_days=int(expiry_days),
            raw=True,
            connection=connection,
        )
        logger.info(f"LEM license_expiry project={project_id} days={expiry_days}")
        return format_success_response({"devices": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM license_expiry failed: {e}", exc_info=True)
        return format_error_response("lem_license_expiry_failed", str(e))


async def lem_get_expired_licenses_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """List devices in a LEM project whose license has already expired."""
    try:
        connection = get_lem_connection(request)
        project_id = get_lem_project_id(request, arguments)
        result = get_expired_licenses(
            project_id=project_id, raw=True, connection=connection
        )
        logger.info(f"LEM expired_licenses project={project_id}")
        return format_success_response({"devices": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM expired_licenses failed: {e}", exc_info=True)
        return format_error_response("lem_expired_licenses_failed", str(e))


async def lem_dashboard_usage_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """Get the project-level usage summary shown on the LEM dashboard."""
    try:
        connection = get_lem_connection(request)
        project_id = get_lem_project_id(request, arguments)
        result = dashboard_usage(
            project_id=project_id, raw=True, connection=connection
        )
        logger.info(f"LEM dashboard_usage project={project_id}")
        return format_success_response({"usage": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM dashboard_usage failed: {e}", exc_info=True)
        return format_error_response("lem_dashboard_usage_failed", str(e))


async def lem_get_project_alerts_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """List active alerts raised in a LEM project."""
    try:
        connection = get_lem_connection(request)
        project_id = get_lem_project_id(request, arguments)
        result = get_project_alerts(
            project_id=project_id, raw=True, connection=connection
        )
        logger.info(f"LEM project_alerts project={project_id}")
        return format_success_response({"alerts": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM project_alerts failed: {e}", exc_info=True)
        return format_error_response("lem_project_alerts_failed", str(e))


_PROJECT_ID_SCHEMA = {
    "type": "string",
    "description": (
        "LEM project id. Optional if EDGE_MANAGER_PROJECT_ID is set in headers."
    ),
}


TOOLS = [
    {
        "name": "lem_list_devices",
        "category": "lem.fleet",
        "description": (
            "Lists edge devices registered in a Litmus Edge Manager (LEM) project, "
            "paginated. Use this to enumerate the fleet from the LEM cloud side. "
            "Returns a page of device records with ids, names, status, and metadata."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID_SCHEMA,
                "page": {
                    "type": "integer",
                    "description": "Zero-indexed page number (default 0).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Page size (default 10).",
                },
                "status": {
                    "type": "string",
                    "description": "Device status filter (default 'ACTIVE').",
                },
            },
            "required": [],
        },
        "handler": lem_list_devices_tool,
    },
    {
        "name": "lem_get_device_details",
        "category": "lem.fleet",
        "description": (
            "Fetches the full LEM-side record for a specific edge device "
            "(versions, license, last seen, configuration). "
            "Use when you have a device id and need its current LEM state."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "LEM device id to fetch.",
                },
                "project_id": _PROJECT_ID_SCHEMA,
            },
            "required": ["device_id"],
        },
        "handler": lem_get_device_details_tool,
    },
    {
        "name": "lem_list_device_versions",
        "category": "lem.fleet",
        "description": (
            "Lists Litmus Edge versions registered in a LEM project. "
            "Use this to see which firmware/build versions are tracked in the cloud."
        ),
        "schema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID_SCHEMA},
            "required": [],
        },
        "handler": lem_list_device_versions_tool,
    },
    {
        "name": "lem_list_device_groups",
        "category": "lem.fleet",
        "description": (
            "Lists device group labels (project-level groupings) defined in a LEM project. "
            "These are NOT driver TAGs - they are organizational tags used to group devices. "
            "Use to discover available groups before filtering or targeting devices."
        ),
        "schema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID_SCHEMA},
            "required": [],
        },
        "handler": lem_list_device_groups_tool,
    },
    {
        "name": "lem_get_license_expiry",
        "category": "lem.licensing",
        "description": (
            "Lists devices in a LEM project whose license will expire within the next N days. "
            "Use for proactive license renewal planning. Returns device records with license info."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "expiry_days": {
                    "type": "integer",
                    "description": "Number of days from now to look ahead (e.g., 30).",
                },
                "project_id": _PROJECT_ID_SCHEMA,
            },
            "required": ["expiry_days"],
        },
        "handler": lem_get_license_expiry_tool,
    },
    {
        "name": "lem_get_expired_licenses",
        "category": "lem.licensing",
        "description": (
            "Lists devices in a LEM project whose license has already expired. "
            "Use to audit which devices are running on lapsed licenses."
        ),
        "schema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID_SCHEMA},
            "required": [],
        },
        "handler": lem_get_expired_licenses_tool,
    },
    {
        "name": "lem_dashboard_usage",
        "category": "lem.dashboard",
        "description": (
            "Returns the LEM project usage summary (device counts, license usage, "
            "deployment stats). Equivalent to the LEM web dashboard view. "
            "Use as a quick health/scale check on a project."
        ),
        "schema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID_SCHEMA},
            "required": [],
        },
        "handler": lem_dashboard_usage_tool,
    },
    {
        "name": "lem_get_project_alerts",
        "category": "lem.dashboard",
        "description": (
            "Lists active project-level alerts in LEM (device offline, license issues, etc.). "
            "Use to surface what needs attention across the fleet right now."
        ),
        "schema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID_SCHEMA},
            "required": [],
        },
        "handler": lem_get_project_alerts_tool,
    },
]
