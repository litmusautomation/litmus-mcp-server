from config import logger
from utils.auth import get_litmus_connection
from utils.formatting import format_success_response, format_error_response

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS
from mcp.types import TextContent
from starlette.requests import Request
from litmussdk.system import network, device_management


async def get_litmusedge_friendly_name(request: Request) -> list[TextContent]:
    """Gets the human-readable name of this Litmus Edge device."""
    try:
        connection = get_litmus_connection(request)
        friendly_name = network.get_friendly_name(connection=connection)

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
        network.set_friendly_name(new_friendly_name, connection=connection)

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


async def get_cloud_activation_status(request: Request) -> list[TextContent]:
    """Checks cloud registration and activation status with Litmus Edge Manager."""
    try:
        connection = get_litmus_connection(request)
        status = device_management.show_cloud_registration_status(
            connection=connection
        )

        logger.info("Retrieved cloud activation status")

        result = {"cloud_status": status}
        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error retrieving cloud status: {e}", exc_info=True)
        return format_error_response("retrieval_failed", str(e))
