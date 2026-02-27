from config import logger
from starlette.requests import Request
from mcp.types import TextContent
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS
from litmussdk.marketplace import list_all_containers, run_container

from utils.auth import get_litmus_connection
from utils.formatting import format_success_response, format_error_response


async def get_all_containers_on_litmusedge(request: Request) -> list[TextContent]:
    """Lists all Docker containers running in the Litmus Edge marketplace."""
    try:
        connection = get_litmus_connection(request)
        container_list = list_all_containers(connection=connection)

        logger.info(f"Retrieved {len(container_list)} containers")

        result = {
            "count": len(container_list),
            "containers": container_list,
        }
        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error retrieving containers: {e}", exc_info=True)
        return format_error_response("retrieval_failed", str(e), count=0, containers=[])


async def run_docker_container_on_litmusedge(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Deploys and runs a new Docker container on the Litmus Edge marketplace.

    SECURITY NOTE: Ensure the container image is trusted and command is validated.
    """
    try:
        docker_run_command = arguments.get("docker_run_command")

        if not docker_run_command:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="'docker_run_command' parameter is required",
                )
            )

        connection = get_litmus_connection(request)
        result = run_container(docker_run_command, connection=connection)

        container_id = result.get("id", "Unknown container ID")

        logger.info(f"Deployed container: {container_id}")

        response = {
            "container_id": container_id,
            "command": docker_run_command,
            "result": result,
        }
        return format_success_response(response)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error running container: {e}", exc_info=True)
        return format_error_response("deployment_failed", str(e))
