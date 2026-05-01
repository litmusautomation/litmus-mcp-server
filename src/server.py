import logging
import asyncio
import os

from contextvars import ContextVar
from mcp.server import Server

from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from config import MCP_PORT
from tools.devicehub_tools import (
    get_litmusedge_driver_list,
    get_devicehub_devices,
    create_devicehub_device,
    get_devicehub_device_tags,
    get_current_value_of_devicehub_tag,
    get_device_connection_status,
    create_devicehub_tag,
    update_devicehub_tag,
    delete_devicehub_tag,
    get_tag_status,
    get_all_tags_status,
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
    list_influxdb_measurements,
    get_device_historical_data,
    query_tag_data,
    get_tag_statistics,
    get_device_data_for_inference,
)
from tools.system_tools import (
    get_device_logs,
    get_system_event_stats,
    get_firewall_rules,
    get_network_interface_info,
    get_packet_capture_interfaces,
    get_packet_capture_status,
    start_packet_capture,
    stop_packet_capture,
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
from tools.resource_tools import (
    get_documentation_resource_list,
    read_documentation_resource,
)

# Set up logging
import warnings
import urllib3
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logging.getLogger(__name__).setLevel(logging.INFO)
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
                "Retrieves tags (data points/registers) with their configuration. "
                "If device_name is provided, returns tags for that device only. "
                "If device_name is omitted, returns tags across ALL devices. "
                "Always performs a count check first — if the total exceeds 1000 the "
                "tags are NOT returned and you should inform the user of the count and "
                "ask them to specify a device_name to narrow the query."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "Name of the device to filter by (omit to query all devices)",
                    },
                },
                "required": [],
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
        # ── DeviceHub connection status ───────────────────────────────────────
        Tool(
            name="get_device_connection_status",
            description=(
                "Checks whether DeviceHub devices are actively publishing data by probing InfluxDB "
                "for recent records. Returns connected/stale/no_data per device. "
                "Use this to diagnose disconnected or silent devices."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Specific device to check (omit for all devices)"},
                    "threshold_seconds": {"type": "integer", "description": "Age threshold in seconds to consider connected (default 60)", "default": 60},
                },
                "required": [],
            },
        ),
        # ── DeviceHub tag CRUD ────────────────────────────────────────────────
        Tool(
            name="create_devicehub_tag",
            description=(
                "Creates a new tag (register) on a DeviceHub device. "
                "register_name is the driver-specific register type (e.g. 'S' for Generator, "
                "'HoldingRegister' for Modbus). Required driver properties (address, count, "
                "pollingInterval, etc.) auto-fill from driver defaults; pass `properties` to "
                "override individual fields. Use get_litmusedge_driver_list to find drivers "
                "and get_devicehub_device_tags to see existing tags."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Name of the device to add the tag to"},
                    "register_name": {"type": "string", "description": "Driver register type (e.g. 'S' for Generator, 'HoldingRegister' for Modbus)"},
                    "tag_name": {"type": "string", "description": "Display name for the tag"},
                    "value_type": {"type": "string", "description": "Data type (e.g. 'float64', 'int64', 'bit', 'string')"},
                    "description": {"type": "string", "description": "Optional description"},
                    "properties": {"type": "object", "description": "Optional driver-specific overrides (e.g. {\"address\": \"5\", \"pollingInterval\": \"500\"}). Missing required fields are filled from driver defaults."},
                },
                "required": ["device_name", "register_name", "tag_name", "value_type"],
            },
        ),
        Tool(
            name="update_devicehub_tag",
            description=(
                "Updates mutable fields of an existing DeviceHub tag: display name, description, or properties. "
                "The device and tag must already exist. Use get_devicehub_device_tags to find tag names."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Name of the device owning the tag"},
                    "tag_name": {"type": "string", "description": "Current display name of the tag to update"},
                    "new_tag_name": {"type": "string", "description": "New display name (optional)"},
                    "description": {"type": "string", "description": "New description (optional)"},
                    "properties": {"type": "object", "description": "New properties dict (optional)"},
                },
                "required": ["device_name", "tag_name"],
            },
        ),
        Tool(
            name="delete_devicehub_tag",
            description=(
                "Deletes a tag from a DeviceHub device. This is destructive and cannot be undone. "
                "Use get_devicehub_device_tags to confirm the tag name before deleting."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Name of the device owning the tag"},
                    "tag_name": {"type": "string", "description": "Display name of the tag to delete"},
                },
                "required": ["device_name", "tag_name"],
            },
        ),
        Tool(
            name="get_tag_status",
            description=(
                "Returns OK/ERROR status for tags on a specific device. "
                "Optionally filter to a single tag by name. "
                "Use this to diagnose which tags are failing on a device."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Name of the device to check"},
                    "tag_name": {"type": "string", "description": "Optional: check a single tag by name"},
                },
                "required": ["device_name"],
            },
        ),
        Tool(
            name="get_all_tags_status",
            description=(
                "Returns tag status across ALL devices. Defaults to returning only non-OK tags "
                "so the LLM sees actionable issues first. Pass filter_status='' to see all. "
                "Use get_tag_status for a single device."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filter_status": {
                        "type": "string",
                        "description": "Filter by state: 'not_ok' (default), 'OK', 'ERROR', or '' for all",
                        "default": "not_ok",
                    },
                },
                "required": [],
            },
        ),
        # ── InfluxDB tools ────────────────────────────────────────────────────
        Tool(
            name="list_influxdb_measurements",
            description=(
                "Lists all measurement names in the InfluxDB tsdata database. "
                "Use this to discover available data series before querying historical data. "
                "Measurement names are typically NATS topic strings."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_device_historical_data",
            description=(
                "Queries historical InfluxDB data using fuzzy device name matching. "
                "Use this when you know a device name but not the exact InfluxDB measurement. "
                "For precise measurement queries use get_historical_data_from_influxdb instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_query": {"type": "string", "description": "Device or measurement name to search for (fuzzy matched)"},
                    "tag_name_query": {"type": "string", "description": "Optional: further filter matches by tag name substring"},
                    "time_range": {"type": "string", "description": "InfluxDB duration (e.g. '1h', '24h', '7d'). Default '1h'", "default": "1h"},
                    "limit": {"type": "integer", "description": "Max records per measurement (default 1000, max 100000)", "default": 1000},
                },
                "required": ["device_query"],
            },
        ),
        Tool(
            name="query_tag_data",
            description=(
                "Queries historical time-series data for a specific tag by looking up its InfluxDB topic. "
                "Returns data ordered newest first. "
                "Use get_tag_statistics for aggregated stats instead of raw samples."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Device that owns the tag"},
                    "tag_name": {"type": "string", "description": "Tag display name (use this or tag_id)"},
                    "tag_id": {"type": "string", "description": "Tag UUID (alternative to tag_name)"},
                    "time_range": {"type": "string", "description": "InfluxDB duration (default '1h')", "default": "1h"},
                    "limit": {"type": "integer", "description": "Max records (default 500, max 500)", "default": 500},
                },
                "required": ["device_name"],
            },
        ),
        Tool(
            name="get_tag_statistics",
            description=(
                "Returns aggregate statistics for a tag: mean, min, max, stddev, count, and baseline range (mean±2σ). "
                "Use this for anomaly detection or understanding normal operating range. "
                "Use query_tag_data to get raw samples instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Device that owns the tag"},
                    "tag_name": {"type": "string", "description": "Tag display name (use this or tag_id)"},
                    "tag_id": {"type": "string", "description": "Tag UUID (alternative to tag_name)"},
                    "time_range": {"type": "string", "description": "InfluxDB duration (default '1h')", "default": "1h"},
                },
                "required": ["device_name"],
            },
        ),
        Tool(
            name="get_device_data_for_inference",
            description=(
                "Comprehensive data package for AI inference: device metadata, all tags, per-tag statistics, "
                "and recent samples in one call. Preferred when asking the AI to analyze or diagnose a device. "
                "Use get_tag_statistics or query_tag_data for single-tag queries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Device to gather data for"},
                    "time_range": {"type": "string", "description": "InfluxDB duration (default '1h')", "default": "1h"},
                    "include_statistics": {"type": "boolean", "description": "Include per-tag statistics (default true)", "default": True},
                    "sample_size": {"type": "integer", "description": "Recent samples per tag (default 20, max 100)", "default": 20},
                },
                "required": ["device_name"],
            },
        ),
        # ── System tools ──────────────────────────────────────────────────────
        Tool(
            name="get_device_logs",
            description=(
                "Retrieves system events and logs from Litmus Edge. "
                "Filter by time range, component, and severity. "
                "Use get_system_event_stats for queue health and throughput metrics instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "from_timestamp": {"type": "integer", "description": "Start time as Unix epoch seconds (default: 1 hour ago)"},
                    "to_timestamp": {"type": "integer", "description": "End time as Unix epoch seconds (default: now)"},
                    "component": {"type": "string", "description": "Filter by component name (optional)"},
                    "severity": {"type": "string", "description": "Filter by severity: INFO, WARN, ALERT, or ERROR (optional)"},
                    "limit": {"type": "integer", "description": "Max events to return (default 100, max 1000)", "default": 100},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_system_event_stats",
            description=(
                "Returns event manager statistics: queue sizes, processing rates, memory, health indicators. "
                "Use this to check system health and event pipeline throughput. "
                "Use get_device_logs to read actual event messages."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_firewall_rules",
            description=(
                "Returns the firewall rules configured on this Litmus Edge device: "
                "ports, protocols, and ALLOW/DENY actions. "
                "Use this to diagnose network connectivity or security configuration."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_network_interface_info",
            description=(
                "Returns network interface details for the Litmus Edge device: "
                "IP address, MAC, gateway, link status, MTU, and speed. "
                "Defaults to eth0. Use this to check network configuration."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "description": "Interface name (default 'eth0')", "default": "eth0"},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_packet_capture_interfaces",
            description=(
                "Lists network interfaces available for packet capture on Litmus Edge "
                "(e.g. eth0, wlan0). Use this before starting a capture to pick the right interface."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_packet_capture_status",
            description=(
                "Returns the current packet capture state and list of captured .pcap files with metadata. "
                "Use start_packet_capture / stop_packet_capture to control capture."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="start_packet_capture",
            description=(
                "Starts a packet capture on a Litmus Edge network interface. "
                "Duration is 1–30 minutes. Let it run to completion — the pcap file "
                "is only retained when the capture finishes naturally. Use "
                "get_packet_capture_status to check progress."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "description": "Interface to capture on (default 'eth0')", "default": "eth0"},
                    "duration": {"type": "integer", "description": "Capture duration in minutes (1–30, default 1)", "default": 1},
                },
                "required": [],
            },
        ),
        Tool(
            name="stop_packet_capture",
            description=(
                "Stops an in-progress packet capture on Litmus Edge. "
                "WARNING: stopping early discards the pcap file — only use this to abort "
                "a capture you don't want. To keep the pcap, let start_packet_capture run "
                "to completion instead. "
                "Use get_packet_capture_status to confirm state before and after."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@mcp.list_resources()
async def handle_list_resources():
    """List all available documentation resources."""
    from mcp.types import Resource

    return [
        Resource(
            uri=doc["uri"],
            name=doc["name"],
            description=doc["description"],
            mimeType=doc.get("mimeType", "text/plain"),
        )
        for doc in get_documentation_resource_list()
    ]


@mcp.read_resource()
async def handle_read_resource(uri):
    """Read a specific documentation resource."""
    from mcp.server.lowlevel.helper_types import ReadResourceContents

    text_contents = await read_documentation_resource(uri)
    return [ReadResourceContents(content=t, mime_type="text/plain") for t in text_contents]


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

        # DeviceHub connection + tag CRUD + tag status
        elif name == "get_device_connection_status":
            return await get_device_connection_status(request, args)
        elif name == "create_devicehub_tag":
            return await create_devicehub_tag(request, args)
        elif name == "update_devicehub_tag":
            return await update_devicehub_tag(request, args)
        elif name == "delete_devicehub_tag":
            return await delete_devicehub_tag(request, args)
        elif name == "get_tag_status":
            return await get_tag_status(request, args)
        elif name == "get_all_tags_status":
            return await get_all_tags_status(request, args)

        # InfluxDB measurement + tag query tools
        elif name == "list_influxdb_measurements":
            return await list_influxdb_measurements(request, args)
        elif name == "get_device_historical_data":
            return await get_device_historical_data(request, args)
        elif name == "query_tag_data":
            return await query_tag_data(request, args)
        elif name == "get_tag_statistics":
            return await get_tag_statistics(request, args)
        elif name == "get_device_data_for_inference":
            return await get_device_data_for_inference(request, args)

        # System events, network, and packet capture
        elif name == "get_device_logs":
            return await get_device_logs(request, args)
        elif name == "get_system_event_stats":
            return await get_system_event_stats(request, args)
        elif name == "get_firewall_rules":
            return await get_firewall_rules(request, args)
        elif name == "get_network_interface_info":
            return await get_network_interface_info(request, args)
        elif name == "get_packet_capture_interfaces":
            return await get_packet_capture_interfaces(request, args)
        elif name == "get_packet_capture_status":
            return await get_packet_capture_status(request, args)
        elif name == "start_packet_capture":
            return await start_packet_capture(request, args)
        elif name == "stop_packet_capture":
            return await stop_packet_capture(request, args)

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


async def run_stdio_server():
    """Run the MCP server using stdio transport."""
    # Create stdio-compatible request context with env-based auth
    stdio_request = StdioRequestContext()
    current_request.set(stdio_request)

    # Log startup
    logger.info("Starting Litmus MCP Server in STDIO mode")
    logger.info("Configuration from environment variables: EDGE_URL, EDGE_API_CLIENT_ID, EDGE_API_CLIENT_SECRET, NATS_*, INFLUX_*")

    # Run with stdio transport
    async with stdio_server() as (read_stream, write_stream):
        await mcp.run(read_stream, write_stream, mcp.create_initialization_options())


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


class StdioRequestContext:
    """Request context for STDIO mode that reads credentials from environment variables."""

    def __init__(self):
        self.headers = HeaderDict({
            "EDGE_API_CLIENT_ID": os.getenv("EDGE_API_CLIENT_ID", ""),
            "EDGE_API_CLIENT_SECRET": os.getenv("EDGE_API_CLIENT_SECRET", ""),
            "EDGE_URL": os.getenv("EDGE_URL", ""),
            "NATS_SOURCE": os.getenv("NATS_SOURCE", ""),
            "NATS_PORT": os.getenv("NATS_PORT", ""),
            "NATS_USER": os.getenv("NATS_USER", ""),
            "NATS_PASSWORD": os.getenv("NATS_PASSWORD", ""),
            "NATS_TLS": os.getenv("NATS_TLS", "true"),
            "INFLUX_HOST": os.getenv("INFLUX_HOST", ""),
            "INFLUX_PORT": os.getenv("INFLUX_PORT", ""),
            "INFLUX_DB_NAME": os.getenv("INFLUX_DB_NAME", ""),
            "INFLUX_USERNAME": os.getenv("INFLUX_USERNAME", ""),
            "INFLUX_PASSWORD": os.getenv("INFLUX_PASSWORD", "")
        })
        self.scope = {}  # Empty scope for compatibility


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


# OAuth discovery endpoint handlers
# These return JSON responses indicating OAuth is not supported
# This prevents MCP clients from getting 404 plain text errors when attempting OAuth discovery

async def oauth_not_supported(request: Request):
    """Return JSON error indicating OAuth is not supported by this server."""
    return JSONResponse(
        status_code=404,
        content={
            "error": "unsupported_oauth",
            "error_description": "This MCP server does not support OAuth authentication. "
                               "Please use SSE transport with header-based authentication "
                               "(EDGE_API_CLIENT_ID and EDGE_API_CLIENT_SECRET)."
        }
    )

async def health_check(request: Request):
    """Basic health check endpoint."""
    return JSONResponse({"status": "ok", "service": "litmus-mcp-server"})

# Wrap the SSE POST handler with our context-capturing middleware
wrapped_post_handler = ContextCapturingMiddleware(sse.handle_post_message)

# Create Starlette app with both SSE and POST message routes
app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages", app=wrapped_post_handler),
        # OAuth discovery endpoints - return proper JSON errors
        Route("/.well-known/oauth-authorization-server", endpoint=oauth_not_supported, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server/sse", endpoint=oauth_not_supported, methods=["GET"]),
        Route("/.well-known/openid-configuration", endpoint=oauth_not_supported, methods=["GET"]),
        Route("/.well-known/openid-configuration/sse", endpoint=oauth_not_supported, methods=["GET"]),
        Route("/sse/.well-known/openid-configuration", endpoint=oauth_not_supported, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", endpoint=oauth_not_supported, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/sse", endpoint=oauth_not_supported, methods=["GET"]),
        Route("/register", endpoint=oauth_not_supported, methods=["GET", "POST"]),
        # Health check endpoint
        Route("/health", endpoint=health_check, methods=["GET"]),
    ]
)

if __name__ == "__main__":
    import uvicorn
    from config import ENABLE_STDIO

    if ENABLE_STDIO:
        # STDIO mode - runs on stdin/stdout
        logger.info("STDIO mode enabled")
        asyncio.run(run_stdio_server())
    else:
        # SSE mode - runs HTTP server (current behavior)
        logger.info(f"SSE mode enabled - Starting on port {MCP_PORT}")
        logger.info(f"SSE endpoint: http://0.0.0.0:{MCP_PORT}/sse")
        uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
