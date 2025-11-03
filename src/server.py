import logging

from contextvars import ContextVar
from mcp.server import Server

from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response

from config import MCP_PORT
from tools.devicehub_tools import (
    get_litmusedge_driver_list,
    get_devicehub_devices,
    create_devicehub_device,
    get_devicehub_device_tags,
    get_current_value_of_devicehub_tag,
)
from tools.dm_tools import (
    get_litmusedge_friendly_name,
    set_litmusedge_friendly_name,
    get_cloud_activation_status,
)
from tools.marketplace_tools import (
    get_all_containers_on_litmusedge,
    run_docker_container_on_litmusedge,
)
from tools.data_tools import (
    get_current_value_on_topic_tool,
    get_multiple_values_from_topic_tool,
    get_historical_data_from_influxdb_tool,
)
from tools.digitaltwins_tools import (
    list_digital_twin_models_tool,
    list_digital_twin_instances_tool,
    create_digital_twin_instance_tool,
    list_static_attributes_tool,
    list_dynamic_attributes_tool,
    list_transformations_tool,
    get_hierarchy_tool,
    save_hierarchy_tool,
)

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Create MCP server
mcp = Server("LitmusMCPServer")

# Context variable to store request across async calls
current_request: ContextVar[Request | None] = ContextVar(
    "current_request", default=None
)


def get_tool_definitions() -> list[Tool]:
    """Return all available tool definitions."""
    return [
        Tool(
            name="get_litmusedge_driver_list",
            description=(
                "Retrieves all available drivers supported by Litmus Edge DeviceHub. "
                "Returns a list of supported industrial protocols and device drivers "
                "(e.g., ModbusTCP, OPCUA, BACnet, MQTT). Use this before creating new "
                "devices to see what drivers are available."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="get_devicehub_devices",
            description=(
                "Retrieves all configured devices in the DeviceHub module on Litmus Edge. "
                "Returns detailed information about each device including name, driver type, "
                "connection settings, and status. Supports filtering by driver."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filter_by_driver": {
                        "type": "string",
                        "description": "Optional: Filter devices by driver name (e.g., 'ModbusTCP')",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="create_devicehub_device",
            description=(
                "Creates a new device in DeviceHub with specified driver and default configuration. "
                "IMPORTANT: This only creates the device with default settings. You'll need to: "
                "1) Update connection properties (IP, port, slave ID, etc.), "
                "2) Configure tags/registers, and 3) Enable the device."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Descriptive name for the device (e.g., 'ProductionLine_PLC1')",
                    },
                    "selected_driver": {
                        "type": "string",
                        "description": "Driver name from get_litmusedge_driver_list() (e.g., 'ModbusTCP')",
                    },
                },
                "required": ["name", "selected_driver"],
            },
        ),
        Tool(
            name="get_devicehub_device_tags",
            description=(
                "Retrieves all tags (data points/registers) configured for a specific device. "
                "Returns tag configuration including name, address, data type, scaling, etc. "
                "Use this to see what data points are available before reading values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "Exact name of the device (get from get_devicehub_devices first)",
                    },
                },
                "required": ["device_name"],
            },
        ),
        Tool(
            name="get_current_value_of_devicehub_tag",
            description=(
                "Reads the current real-time value of a specific tag from a device. "
                "Returns the value along with timestamp and quality. "
                "You must provide either tag_name OR tag_id (not both)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "Name of the device containing the tag",
                    },
                    "tag_name": {
                        "type": "string",
                        "description": "Human-readable name of the tag (preferred method)",
                    },
                    "tag_id": {
                        "type": "string",
                        "description": "Unique ID of the tag (alternative if tag_name unknown)",
                    },
                },
                "required": ["device_name"],
            },
        ),
        Tool(
            name="get_litmusedge_friendly_name",
            description=(
                "Gets the human-readable name assigned to this Litmus Edge device. "
                "Use this to identify which Edge device you're working with."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="set_litmusedge_friendly_name",
            description=(
                "Changes the human-readable name of this Litmus Edge device. "
                "Use this to give the device a more descriptive name or update naming "
                "after device relocation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "new_friendly_name": {
                        "type": "string",
                        "description": "New descriptive name (e.g., 'Building_A_Gateway')",
                    },
                },
                "required": ["new_friendly_name"],
            },
        ),
        Tool(
            name="get_cloud_activation_status",
            description=(
                "Checks the cloud registration and activation status with Litmus Edge Manager. "
                "Returns connection state, last sync time, and any error messages. "
                "Use this to verify cloud connectivity and troubleshoot sync issues."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="get_all_containers_on_litmusedge",
            description=(
                "Lists all Docker containers running in the Litmus Edge marketplace. "
                "Returns container details including name, image, status, ports, and resource usage. "
                "Use this to see what applications are running on the Edge."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="run_docker_container_on_litmusedge",
            description=(
                "Deploys and runs a new Docker container on the Litmus Edge marketplace. "
                "IMPORTANT: This runs on the Edge device, not the MCP server host. "
                "SECURITY NOTE: Ensure the container image is trusted and command is validated."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "docker_run_command": {
                        "type": "string",
                        "description": "Complete docker run command (e.g., 'docker run -d --name myapp -p 8080:8080 myimage:latest')",
                    },
                },
                "required": ["docker_run_command"],
            },
        ),
        Tool(
            name="get_current_value_from_topic",
            description=(
                "Gets the current value from a NATS topic. "
                "Subscribes to the topic and returns the next published message. "
                "Note: User may refer to NATS topics as 'datahub subscribe topic' or 'pubsub topic'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "NATS topic to subscribe to (also called datahub subscribe topic or pubsub topic)",
                    },
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="get_multiple_values_from_topic",
            description=(
                "Collects multiple sequential values from a NATS topic for trend analysis or plotting. "
                "WARNING: This function blocks until num_samples messages are received. "
                "Use this for time-series data collection, trend analysis, or creating charts. "
                "Does NOT retrieve historical data - waits for new messages. "
                "Note: User may refer to NATS topics as 'datahub subscribe topic' or 'pubsub topic'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "NATS topic to monitor (also called datahub subscribe topic or pubsub topic)",
                    },
                    "num_samples": {
                        "type": "integer",
                        "description": "Number of messages to collect (default: 10, max: 100)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "nats_source": {
                        "type": "string",
                        "description": "Optional: NATS broker IP (default: 10.30.50.1)",
                    },
                    "nats_port": {
                        "type": "string",
                        "description": "Optional: NATS broker port (default: 4222)",
                    },
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="get_historical_data_from_influxdb",
            description=(
                "Queries historical time-series data from InfluxDB. "
                "Retrieve past data, historic trends, or perform data analysis on stored values. "
                "User provides the measurement name and how much historical data they want. "
                "Note: This retrieves PAST data already stored in the database, "
                "unlike get_multiple_values_from_topic which waits for NEW messages."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "measurement": {
                        "type": "string",
                        "description": "Measurement/variable name in InfluxDB (e.g., 'variable0', 'temperature', 'pressure'). "
                        "This is the name of the data series you want to retrieve.",
                    },
                    "time_range": {
                        "type": "string",
                        "description": "How much historical data to retrieve (e.g., '5m', '1h', '24h', '7d', '30d'). "
                        "Examples: '5m' = last 5 minutes, '1h' = last hour, '24h' = last day. Default: '1h'",
                        "default": "1h",
                    },
                },
                "required": ["measurement"],
            },
        ),
        Tool(
            name="list_digital_twin_models",
            description=(
                "Lists all Digital Twin models configured on Litmus Edge. "
                "Returns model information including ID, name, description, and version. "
                "Use this to see available models before creating instances or managing attributes."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="list_digital_twin_instances",
            description=(
                "Lists all Digital Twin instances or instances for a specific model. "
                "Instances are runtime representations of models with actual data. "
                "Can optionally filter by model_id to get only instances of a specific model."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "Optional: Filter instances by model ID. If not provided, returns all instances.",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="create_digital_twin_instance",
            description=(
                "Creates a new Digital Twin instance from an existing model. "
                "An instance is a runtime representation of a model that processes and publishes data. "
                "Requires model_id, instance name, and NATS topic for data publication."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "ID of the model to instantiate (from list_digital_twin_models)",
                    },
                    "instance_name": {
                        "type": "string",
                        "description": "Descriptive name for the new instance",
                    },
                    "instance_topic": {
                        "type": "string",
                        "description": "NATS topic where the instance will publish its data",
                    },
                    "instance_interval": {
                        "type": "integer",
                        "description": "Optional: Data publication interval in seconds (default: 1)",
                        "default": 1,
                    },
                    "instance_flat_hierarchy": {
                        "type": "boolean",
                        "description": "Optional: Use flat hierarchy structure (default: false)",
                        "default": False,
                    },
                },
                "required": ["model_id", "instance_name", "instance_topic"],
            },
        ),
        Tool(
            name="list_static_attributes",
            description=(
                "Lists static attributes for a Digital Twin model or instance. "
                "Static attributes are fixed key-value pairs (e.g., serial number, location). "
                "Must provide either model_id OR instance_id (not both)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "Model ID to get static attributes from (exclusive with instance_id)",
                    },
                    "instance_id": {
                        "type": "string",
                        "description": "Instance ID to get static attributes from (exclusive with model_id)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="list_dynamic_attributes",
            description=(
                "Lists dynamic attributes for a Digital Twin model or instance. "
                "Dynamic attributes are real-time data points (e.g., temperature, pressure, speed). "
                "Must provide either model_id OR instance_id (not both)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "Model ID to get dynamic attributes from (exclusive with instance_id)",
                    },
                    "instance_id": {
                        "type": "string",
                        "description": "Instance ID to get dynamic attributes from (exclusive with model_id)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="list_transformations",
            description=(
                "Lists transformations configured for a Digital Twin model. "
                "Transformations define data processing rules and calculations within the model. "
                "Returns transformation schemas showing how data is transformed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "Model ID to get transformations from",
                    },
                },
                "required": ["model_id"],
            },
        ),
        Tool(
            name="get_digital_twin_hierarchy",
            description=(
                "Gets the hierarchy configuration for a Digital Twin model. "
                "The hierarchy defines the structural relationships and organization within the model. "
                "Returns the complete hierarchy structure in JSON format."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "Model ID to get hierarchy from",
                    },
                },
                "required": ["model_id"],
            },
        ),
        Tool(
            name="save_digital_twin_hierarchy",
            description=(
                "Saves a new hierarchy configuration to a Digital Twin model. "
                "The hierarchy must be in the exact JSON format used by Digital Twins. "
                "Use get_digital_twin_hierarchy first to see the expected format."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "Model ID to save hierarchy to",
                    },
                    "hierarchy_json": {
                        "type": "object",
                        "description": "Complete hierarchy configuration in Digital Twins JSON format",
                    },
                },
                "required": ["model_id", "hierarchy_json"],
            },
        ),
    ]


@mcp.list_tools()
async def handle_list_tools() -> list[Tool]:
    """Handle list_tools request."""
    return get_tool_definitions()


@mcp.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """
    Handle tool execution requests and route to appropriate implementations.

    Args:
        name: The name of the tool to execute
        arguments: Tool-specific arguments (None if no arguments provided)

    Returns:
        List of TextContent with results

    Raises:
        ValueError: If tool name is not recognized
        McpError: If tool execution fails
    """
    try:
        # Get request from context
        request = current_request.get()
        if request is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Request context not available",
                )
            )

        # Normalize arguments to empty dict if None
        args = arguments or {}

        # Driver tools
        if name == "get_litmusedge_driver_list":
            return await get_litmusedge_driver_list(request)

        # Device tools
        elif name == "get_devicehub_devices":
            return await get_devicehub_devices(request, args)
        elif name == "create_devicehub_device":
            return await create_devicehub_device(request, args)

        # Tag tools
        elif name == "get_devicehub_device_tags":
            return await get_devicehub_device_tags(request, args)
        elif name == "get_current_value_of_devicehub_tag":
            return await get_current_value_of_devicehub_tag(request, args)

        # System tools
        elif name == "get_litmusedge_friendly_name":
            return await get_litmusedge_friendly_name(request)
        elif name == "set_litmusedge_friendly_name":
            return await set_litmusedge_friendly_name(request, args)
        elif name == "get_cloud_activation_status":
            return await get_cloud_activation_status(request)

        # Container tools
        elif name == "get_all_containers_on_litmusedge":
            return await get_all_containers_on_litmusedge(request)
        elif name == "run_docker_container_on_litmusedge":
            return await run_docker_container_on_litmusedge(request, args)

        # NATS topic tools
        elif name == "get_current_value_from_topic":
            return await get_current_value_on_topic_tool(request, args)
        elif name == "get_multiple_values_from_topic":
            return await get_multiple_values_from_topic_tool(request, args)

        # InfluxDB historical data tool
        elif name == "get_historical_data_from_influxdb":
            logger.info("get_historical_data_from_influxdb")
            return await get_historical_data_from_influxdb_tool(request, args)

        # Digital Twins tools
        elif name == "list_digital_twin_models":
            return await list_digital_twin_models_tool(request)
        elif name == "list_digital_twin_instances":
            return await list_digital_twin_instances_tool(request, args)
        elif name == "create_digital_twin_instance":
            return await create_digital_twin_instance_tool(request, args)
        elif name == "list_static_attributes":
            return await list_static_attributes_tool(request, args)
        elif name == "list_dynamic_attributes":
            return await list_dynamic_attributes_tool(request, args)
        elif name == "list_transformations":
            return await list_transformations_tool(request, args)
        elif name == "get_digital_twin_hierarchy":
            return await get_hierarchy_tool(request, args)
        elif name == "save_digital_twin_hierarchy":
            return await save_hierarchy_tool(request, args)

        else:
            raise ValueError(f"Unknown tool: {name}")

    except McpError:
        # Re-raise MCP errors as-is
        raise
    except ValueError as e:
        # Handle unknown tool errors
        logger.error(f"Unknown tool requested: {name}")
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=str(e),
            )
        ) from e
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Error executing tool {name}: {e}", exc_info=True)
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=f"Tool execution failed: {str(e)}",
            )
        ) from e


# SSE endpoint handler
sse = SseServerTransport("/messages")


# SSE endpoint handler
async def handle_sse(request: Request):
    # Set request in context for this connection
    current_request.set(request)

    async with sse.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp.run(streams[0], streams[1], mcp.create_initialization_options())

    # Return empty response to avoid NoneType error
    return Response()


# Helper classes for header extraction
class HeaderDict:
    """Dict-like object that provides .get() method for headers with case-insensitive lookup."""

    def __init__(self, headers_dict):
        # Store headers with lowercase keys for case-insensitive lookup
        self._headers = {k.lower(): v for k, v in headers_dict.items()}
        # Keep original case for logging
        self._original = headers_dict

    def get(self, key, default=None):
        # Case-insensitive header lookup (HTTP standard)
        return self._headers.get(key.lower(), default)


class HeaderOnlyRequest:
    """Lightweight request object for header extraction."""

    def __init__(self, scope):
        self.scope = scope
        # Parse headers from ASGI scope format
        headers_dict = {}
        for header_name, header_value in scope.get("headers", []):
            headers_dict[header_name.decode("latin-1")] = header_value.decode("latin-1")
        self.headers = HeaderDict(headers_dict)


class ContextCapturingMiddleware:
    """ASGI middleware that captures request headers and sets them in context."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Create a request-like object with headers
        header_request = HeaderOnlyRequest(scope)

        # Set in context before calling the app
        current_request.set(header_request)

        # Delegate to the wrapped app
        await self.app(scope, receive, send)


# Wrap the SSE POST handler with our context-capturing middleware
wrapped_post_handler = ContextCapturingMiddleware(sse.handle_post_message)

# Create Starlette app with both SSE and POST message routes
app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages", app=wrapped_post_handler),
    ]
)

if __name__ == "__main__":
    import uvicorn

    logger.info(f"Starting Litmus MCP Server on port {MCP_PORT}")
    logger.info(f"SSE endpoint: http://0.0.0.0:{MCP_PORT}/sse")

    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
