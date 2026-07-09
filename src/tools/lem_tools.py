from starlette.requests import Request
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, TextContent, ToolAnnotations

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
    deployment_info,
    get_project_alerts,
    get_system_time,
)
from litmussdk.lem.companies import (
    list_all_company_stats,
    get_company_details,
    get_company_projects,
    get_project_details,
)
from litmussdk.utils.conn import new_lem_bridge_connection
from litmussdk.devicehub import devices as devicehub_devices
from litmussdk.system import network, device_management
from config import DEFAULT_TIMEOUT

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
        # The LEM API returns the page as {pageNum, pagesCount, size, totalSize,
        # elements: [...]}. Surface elements/totalSize at the top level so
        # callers don't have to know the shape; keep the raw page nested.
        page_dict = result if isinstance(result, dict) else {}
        devices = page_dict.get("elements") or []
        logger.info(
            f"LEM list_devices project={project_id} page={page} limit={limit} "
            f"returned={len(devices)} total={page_dict.get('totalSize')}"
        )
        return format_success_response(
            {
                "devices": devices,
                "count": len(devices),
                "total_size": page_dict.get("totalSize"),
                "page_num": page_dict.get("pageNum"),
                "pages_count": page_dict.get("pagesCount"),
                "page": page_dict,
            }
        )
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


# ── Company / project navigation ────────────────────────────────────────────


async def lem_list_companies_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """List all companies on this LEM tenant with project/device/model counts."""
    try:
        connection = get_lem_connection(request)
        result = list_all_company_stats(raw=True, connection=connection)
        logger.info(f"LEM list_companies: {len(result) if result else 0} companies")
        return format_success_response(
            {"companies": result, "count": len(result) if result else 0}
        )
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM list_companies failed: {e}", exc_info=True)
        return format_error_response("lem_list_companies_failed", str(e))


async def lem_get_company_details_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Get full details for a specific company by name."""
    try:
        company_name = (arguments or {}).get("company_name")
        if not company_name:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'company_name' parameter is required"
                )
            )
        connection = get_lem_connection(request)
        result = get_company_details(
            company_name=company_name, raw=True, connection=connection
        )
        logger.info(f"LEM company_details: {company_name}")
        return format_success_response({"company": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM company_details failed: {e}", exc_info=True)
        return format_error_response("lem_company_details_failed", str(e))


async def lem_list_company_projects_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """List all projects belonging to a given company."""
    try:
        company_name = (arguments or {}).get("company_name")
        if not company_name:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'company_name' parameter is required"
                )
            )
        connection = get_lem_connection(request)
        result = get_company_projects(
            company_name=company_name, raw=True, connection=connection
        )
        logger.info(
            f"LEM company_projects: company={company_name}, "
            f"{len(result) if result else 0} projects"
        )
        return format_success_response(
            {"projects": result, "count": len(result) if result else 0}
        )
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM company_projects failed: {e}", exc_info=True)
        return format_error_response("lem_company_projects_failed", str(e))


async def lem_get_project_details_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """Get details for a single LEM project (timezone, data TTL, allocated slots, etc.)."""
    try:
        connection = get_lem_connection(request)
        project_id = get_lem_project_id(request, arguments)
        result = get_project_details(
            project_id=project_id, raw=True, connection=connection
        )
        logger.info(f"LEM project_details: {project_id}")
        return format_success_response({"project": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM project_details failed: {e}", exc_info=True)
        return format_error_response("lem_project_details_failed", str(e))


# ── Tenant-level info ──────────────────────────────────────────────────────


async def lem_deployment_info_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """Return deployment info for the LEM tenant (version, build)."""
    try:
        connection = get_lem_connection(request)
        result = deployment_info(raw=True, connection=connection)
        logger.info("LEM deployment_info retrieved")
        return format_success_response({"deployment": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM deployment_info failed: {e}", exc_info=True)
        return format_error_response("lem_deployment_info_failed", str(e))


async def lem_get_system_time_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """Return the LEM server clock; useful when comparing edge timestamps."""
    try:
        connection = get_lem_connection(request)
        result = get_system_time(raw=True, connection=connection)
        logger.info("LEM system_time retrieved")
        return format_success_response({"system_time": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM system_time failed: {e}", exc_info=True)
        return format_error_response("lem_system_time_failed", str(e))


# ── LEM bridge: drill into a specific edge through LEM ─────────────────────


def _build_bridge_connection(request: Request, project_id: str, device_id: str):
    """Build an LE bridge connection from LEM credentials in headers + supplied ids."""
    manager_url = request.headers.get("EDGE_MANAGER_URL", "")
    api_token = request.headers.get("EDGE_API_TOKEN", "")
    validate_certificate = (
        request.headers.get("VALIDATE_CERTIFICATE", "false").lower() == "true"
    )
    if not manager_url:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS, message="EDGE_MANAGER_URL header is required"
            )
        )
    if not api_token:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS, message="EDGE_API_TOKEN header is required"
            )
        )
    return new_lem_bridge_connection(
        edge_manager_url=manager_url,
        edge_api_token=api_token,
        project_id=project_id,
        device_id=device_id,
        validate_certificate=validate_certificate,
        timeout_seconds=DEFAULT_TIMEOUT,
    )


def _require_bridge_args(arguments: dict | None) -> tuple[str, str]:
    args = arguments or {}
    project_id = args.get("project_id")
    device_id = args.get("device_id")
    if not project_id:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS, message="'project_id' parameter is required"
            )
        )
    if not device_id:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS, message="'device_id' parameter is required"
            )
        )
    return project_id, device_id


def _classify_bridge_error(exc: Exception) -> str:
    """Translate raw SDK/JSON errors into a one-word reason for the LLM."""
    msg = str(exc).lower()
    name = type(exc).__name__
    if "json" in name.lower() or "json" in msg or "expecting value" in msg:
        return "edge_unreachable"
    if "validation" in name.lower() or "validation" in msg:
        return "validation_error"
    if "gql" in name.lower() or "graphql" in msg:
        return "graphql_error"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    return "bridge_error"


async def lem_bridge_list_devicehub_devices_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """List devicehub devices on a specific LE through LEM bridge.

    Uses `raw=True` on the SDK list call so quirky edge data still yields a
    usable count instead of a pydantic serialization failure.
    """
    try:
        project_id, device_id = _require_bridge_args(arguments)
        bridge = _build_bridge_connection(request, project_id, device_id)
        try:
            raw_items = (
                devicehub_devices.list_devices(le_connection=bridge, raw=True) or []
            )
        except Exception as e:
            reason = _classify_bridge_error(e)
            logger.warning(
                f"LEM bridge list_devicehub_devices "
                f"project={project_id} device={device_id}: {reason}: {e}"
            )
            return format_error_response(
                f"lem_bridge_{reason}",
                f"Could not reach edge through LEM bridge: {e}",
            )

        items = [
            {
                "id": d.get("ID"),
                "name": d.get("Name"),
                "driver_id": d.get("DriverID"),
                "description": d.get("Description"),
            }
            for d in raw_items
            if isinstance(d, dict)
        ]
        logger.info(
            f"LEM bridge list_devicehub_devices: project={project_id} "
            f"device={device_id} count={len(items)}"
        )
        return format_success_response(
            {"devicehub_devices": items, "count": len(items)}
        )
    except McpError:
        raise
    except Exception as e:
        logger.error(
            f"LEM bridge list_devicehub_devices failed: {e}", exc_info=True
        )
        return format_error_response(
            "lem_bridge_list_devicehub_devices_failed", str(e)
        )


async def lem_bridge_get_le_info_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Return basic identity info (friendly name, cloud activation) for an LE through LEM bridge."""
    try:
        project_id, device_id = _require_bridge_args(arguments)
        bridge = _build_bridge_connection(request, project_id, device_id)
        info: dict = {}
        try:
            info["friendly_name"] = network.get_friendly_name(le_connection=bridge)
        except Exception as e:
            info["friendly_name_error"] = str(e)
        try:
            info["cloud_status"] = device_management.show_cloud_registration_status(
                le_connection=bridge
            )
        except Exception as e:
            info["cloud_status_error"] = str(e)
        logger.info(
            f"LEM bridge le_info: project={project_id} device={device_id}"
        )
        return format_success_response({"le_info": info})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"LEM bridge le_info failed: {e}", exc_info=True)
        return format_error_response("lem_bridge_le_info_failed", str(e))


_PROJECT_ID_SCHEMA = {
    "type": "string",
    "description": (
        "LEM project id. Optional if EDGE_MANAGER_PROJECT_ID is set in headers."
    ),
}

_BRIDGE_PROJECT_ID_SCHEMA = {
    "type": "string",
    "description": "LEM project id of the target edge device.",
}

_BRIDGE_DEVICE_ID_SCHEMA = {
    "type": "string",
    "description": "LEM device id of the target edge device.",
}


TOOLS = [
    {
        "name": "lem_list_devices",
        "category": "lem.fleet",
        "annotations": ToolAnnotations(title="LEM: List Devices", readOnlyHint=True),
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
        "annotations": ToolAnnotations(title="LEM: Get Device Details", readOnlyHint=True),
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
        "annotations": ToolAnnotations(title="LEM: List Device Versions", readOnlyHint=True),
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
        "annotations": ToolAnnotations(title="LEM: List Device Groups", readOnlyHint=True),
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
        "annotations": ToolAnnotations(title="LEM: Get Expiring Licenses", readOnlyHint=True),
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
        "annotations": ToolAnnotations(title="LEM: Get Expired Licenses", readOnlyHint=True),
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
        "annotations": ToolAnnotations(title="LEM: Dashboard Usage", readOnlyHint=True),
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
        "annotations": ToolAnnotations(title="LEM: Get Project Alerts", readOnlyHint=True),
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
    {
        "name": "lem_list_companies",
        "category": "lem.companies",
        "annotations": ToolAnnotations(title="LEM: List Companies", readOnlyHint=True),
        "description": (
            "Lists all companies (tenants) on this LEM with per-company counts of "
            "projects, devices, and models. Top of the LEM hierarchy. Start here when "
            "the user asks about totals across companies or wants a directory view."
        ),
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": lem_list_companies_tool,
    },
    {
        "name": "lem_get_company_details",
        "category": "lem.companies",
        "annotations": ToolAnnotations(title="LEM: Get Company Details", readOnlyHint=True),
        "description": (
            "Gets full details for a single company: real name, description, teams, "
            "license, quotas. Use after lem_list_companies when the user asks for "
            "deeper info on one company."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": (
                        "Company short name (the 'name' field from lem_list_companies, "
                        "not 'real_name')."
                    ),
                },
            },
            "required": ["company_name"],
        },
        "handler": lem_get_company_details_tool,
    },
    {
        "name": "lem_list_company_projects",
        "category": "lem.companies",
        "annotations": ToolAnnotations(title="LEM: List Company Projects", readOnlyHint=True),
        "description": (
            "Lists all projects belonging to a company. Each project is a container "
            "for edge devices. Use as the second step when drilling from a company "
            "down to its devices."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Company short name to list projects for.",
                },
            },
            "required": ["company_name"],
        },
        "handler": lem_list_company_projects_tool,
    },
    {
        "name": "lem_get_project_details",
        "category": "lem.companies",
        "annotations": ToolAnnotations(title="LEM: Get Project Details", readOnlyHint=True),
        "description": (
            "Gets details for one project (timezone, data TTL, allocated slots, "
            "topics, billing plan). Use when the user asks about project-level "
            "configuration or quotas."
        ),
        "schema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID_SCHEMA},
            "required": [],
        },
        "handler": lem_get_project_details_tool,
    },
    {
        "name": "lem_deployment_info",
        "category": "lem.tenant",
        "annotations": ToolAnnotations(title="LEM: Deployment Info", readOnlyHint=True),
        "description": (
            "Returns deployment info for the LEM tenant itself: version, build, "
            "release metadata. Use to verify connectivity or check the LEM version."
        ),
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": lem_deployment_info_tool,
    },
    {
        "name": "lem_get_system_time",
        "category": "lem.tenant",
        "annotations": ToolAnnotations(title="LEM: Get System Time", readOnlyHint=True),
        "description": (
            "Returns the LEM server clock. Use when comparing LEM-reported "
            "timestamps to edge timestamps or local time."
        ),
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": lem_get_system_time_tool,
    },
    {
        "name": "lem_bridge_list_devicehub_devices",
        "category": "lem.bridge",
        "annotations": ToolAnnotations(title="LEM Bridge: List DeviceHub Devices", readOnlyHint=True),
        "description": (
            "Lists devicehub devices configured on a specific edge by tunneling "
            "through LEM. Requires both project_id and device_id (the edge's LEM "
            "ids). Use this to count or enumerate devicehub devices on an edge "
            "without changing the active edge instance. Aggregating across many "
            "edges requires one call per edge."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "project_id": _BRIDGE_PROJECT_ID_SCHEMA,
                "device_id": _BRIDGE_DEVICE_ID_SCHEMA,
            },
            "required": ["project_id", "device_id"],
        },
        "handler": lem_bridge_list_devicehub_devices_tool,
    },
    {
        "name": "lem_bridge_get_le_info",
        "category": "lem.bridge",
        "annotations": ToolAnnotations(title="LEM Bridge: Get Edge Info", readOnlyHint=True),
        "description": (
            "Returns identity info for an edge (friendly name + cloud activation "
            "status) by tunneling through LEM. Useful for translating opaque LEM "
            "device ids into human-readable names."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "project_id": _BRIDGE_PROJECT_ID_SCHEMA,
                "device_id": _BRIDGE_DEVICE_ID_SCHEMA,
            },
            "required": ["project_id", "device_id"],
        },
        "handler": lem_bridge_get_le_info_tool,
    },
]
