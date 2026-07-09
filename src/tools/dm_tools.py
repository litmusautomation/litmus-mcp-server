from config import logger
from utils.auth import get_litmus_connection
from utils.formatting import format_success_response, format_error_response

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS
from mcp.types import TextContent, ToolAnnotations
from starlette.requests import Request
from litmussdk.system import network, device_management


async def get_litmusedge_friendly_name(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """Gets the human-readable name of this Litmus Edge device."""
    try:
        connection = get_litmus_connection(request)
        friendly_name = network.get_friendly_name(le_connection=connection)

        logger.info(f"Retrieved friendly name: {friendly_name}")

        result = {"friendly_name": friendly_name}
        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error retrieving friendly name: {e}", exc_info=True)
        return format_error_response("retrieval_failed", str(e))


async def set_litmusedge_friendly_name(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Changes the human-readable name of this Litmus Edge device."""
    try:
        new_friendly_name = arguments.get("new_friendly_name")

        if not new_friendly_name:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="'new_friendly_name' parameter is required",
                )
            )

        connection = get_litmus_connection(request)
        network.set_friendly_name(new_friendly_name, le_connection=connection)

        logger.info(f"Updated friendly name to: {new_friendly_name}")

        result = {
            "friendly_name": new_friendly_name,
            "message": f"Device friendly name updated to '{new_friendly_name}'",
        }
        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error setting friendly name: {e}", exc_info=True)
        return format_error_response("update_failed", str(e))


async def get_cloud_activation_status(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """Checks cloud registration and activation status with Litmus Edge Manager."""
    try:
        connection = get_litmus_connection(request)
        status = device_management.show_cloud_registration_status(
            le_connection=connection
        )

        logger.info("Retrieved cloud activation status")

        result = {"cloud_status": status}
        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error retrieving cloud status: {e}", exc_info=True)
        return format_error_response("retrieval_failed", str(e))


TOOLS = [
    {
        "name": "get_litmusedge_friendly_name",
        "category": "system.identity",
        "annotations": ToolAnnotations(title="Get Edge Friendly Name", readOnlyHint=True),
        "description": (
            "Gets the human-readable name assigned to this Litmus Edge device. "
            "Use this to identify which Edge device you're working with."
        ),
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": get_litmusedge_friendly_name,
    },
    {
        "name": "set_litmusedge_friendly_name",
        "category": "system.identity",
        "annotations": ToolAnnotations(title="Set Edge Friendly Name", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Changes the human-readable name of this Litmus Edge device. "
            "Use this to give the device a more descriptive name or update naming "
            "after device relocation."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "new_friendly_name": {
                    "type": "string",
                    "description": "New descriptive name (e.g., 'Building_A_Gateway')",
                },
            },
            "required": ["new_friendly_name"],
        },
        "handler": set_litmusedge_friendly_name,
    },
    {
        "name": "get_cloud_activation_status",
        "category": "system.cloud",
        "annotations": ToolAnnotations(title="Get Cloud Activation Status", readOnlyHint=True),
        "description": (
            "Checks the cloud registration and activation status with Litmus Edge Manager. "
            "Returns connection state, last sync time, and any error messages. "
            "Use this to verify cloud connectivity and troubleshoot sync issues."
        ),
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": get_cloud_activation_status,
    },
]
