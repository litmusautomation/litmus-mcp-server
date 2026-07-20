import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, Any
from config import logger
from utils.auth import get_litmus_connection, get_influx_connection_params
from utils.formatting import format_success_response, format_error_response
from .data_tools import (
    get_current_value_on_topic,
    _make_influx_client,
    _influx_connection_note,
    _with_connection_note,
)

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR
from mcp.types import TextContent, ToolAnnotations
from starlette.requests import Request
from litmussdk.devicehub import devices, tags
from litmussdk.devicehub.tags import Tag
from litmussdk.devicehub.drivers import list_all_drivers
from litmussdk.utils import api, api_paths, gql_queries

from .sdk_cli_tools import run_cli_function, CLIFunctionError

# Short-lived cache for the device list, keyed by EDGE_URL.
# Avoids redundant API round-trips when the LLM calls multiple device tools
# back-to-back within the same conversation turn.
_device_list_cache: dict[str, tuple[list, float]] = {}
_DEVICE_LIST_TTL = 10  # seconds


async def get_litmusedge_driver_list(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """
    Retrieves all available drivers supported by Litmus Edge DeviceHub.

    Returns a list of supported industrial protocols and device drivers
    (e.g., ModbusTCP, OPCUA, BACnet, MQTT).
    """
    try:

        connection = get_litmus_connection(request)
        driver_list = list_all_drivers(le_connection=connection)

        drivers = []
        for driver in driver_list:
            driver_info = {
                "name": driver.name,
                "id": getattr(driver, "id", None),
                "protocol": getattr(driver, "protocol", None),
                "version": getattr(driver, "version", None),
                "description": getattr(driver, "description", None),
                "category": getattr(driver, "category", None),
            }
            drivers.append({k: v for k, v in driver_info.items() if v is not None})

        drivers.sort(key=lambda x: x["name"])

        logger.info(f"Retrieved {len(drivers)} drivers from Litmus Edge")

        result = {
            "count": len(drivers),
            "drivers": drivers,
            "driver_names": [d["name"] for d in drivers],
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error retrieving driver list: {e}", exc_info=True)
        return format_error_response("retrieval_failed", str(e))


async def get_devicehub_devices(request: Request, arguments: dict) -> list[TextContent]:
    """
    Retrieves all configured devices in the DeviceHub module.

    Supports filtering by driver type and status, with optional tag inclusion.
    """
    try:
        filter_by_driver = arguments.get("filter_by_driver")

        connection = get_litmus_connection(request)
        device_list = devices.list_devices(le_connection=connection)
        logger.info(f"Retrieved {len(device_list)} devices from Litmus Edge")

        device_data = []
        for current_device in device_list:
            try:
                device_info = _build_device_info(current_device)
            except Exception as e:
                logger.warning(f"Skipping device due to parse error: {e}")
                device_info = {
                    "name": getattr(current_device, "name", str(current_device)),
                    "id": getattr(current_device, "id", None),
                    "parse_error": True,
                }

            if filter_by_driver and device_info.get("driver") != filter_by_driver:
                continue
            device_data.append(device_info)

        device_data.sort(key=lambda x: x.get("name", ""))

        try:
            summary = _create_device_summary(device_data)
        except Exception:
            summary = {}

        result: dict[str, Any] = {
            "count": len(device_data),
            "devices": device_data,
            "summary": summary,
        }
        if filter_by_driver:
            result["filters_applied"] = {"driver": filter_by_driver}

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error retrieving devices: {e}", exc_info=True)
        return format_error_response("retrieval_failed", str(e))


async def create_devicehub_device(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Creates a new device in DeviceHub with specified driver.

    IMPORTANT: Creates device with default settings. You'll need to:
    1. Update connection properties (IP, port, slave ID, etc.)
    2. Configure tags/registers
    3. Enable the device
    """
    try:
        name = arguments.get("name")
        selected_driver = arguments.get("selected_driver")

        if not name:
            raise McpError(
                ErrorData(code=INVALID_PARAMS, message="'name' parameter is required")
            )
        if not selected_driver:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="'selected_driver' parameter is required",
                )
            )

        connection = get_litmus_connection(request)

        # Get driver information
        driver_list = list_all_drivers(le_connection=connection)
        driver_map = {}
        driver_names = []

        for driver in driver_list:
            driver_map[driver.name] = {
                "id": driver.id,
                "properties": driver.get_default_properties(),
            }
            driver_names.append(driver.name)

        if selected_driver not in driver_names:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Driver '{selected_driver}' not found. Available drivers: {driver_names}",
                )
            )

        # Create device
        device = devices.Device(
            name=name,
            properties=driver_map[selected_driver]["properties"],
            driver=driver_map[selected_driver]["id"],
        )

        created_device = devices.create_device(device, le_connection=connection)

        device_dict = (
            created_device.__dict__
            if hasattr(created_device, "__dict__")
            else {"id": str(created_device)}
        )

        logger.info(f"Created device '{name}' with driver '{selected_driver}'")

        result = {
            "device": device_dict,
            "next_steps": [
                "Update connection properties (IP address, port, etc.)",
                "Configure tags/registers for data collection",
                "Enable the device to start communication",
            ],
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error creating device: {e}", exc_info=True)
        return format_error_response("creation_failed", str(e))


_TAG_LIMIT = 1000

_COUNT_DEVICE_TAGS = """
query CountRegisters($input: ListRegistersRequest!) {
    ListRegisters(input: $input) {
        TotalCount
    }
}
"""

_COUNT_ALL_TAGS = """
query CountAllRegisters($input: ListRegistersFromAllDevicesRequest!) {
    ListRegistersFromAllDevices(input: $input) {
        TotalCount
    }
}
"""

_LIST_ALL_TAGS_RAW = """
query ListAllRegisters($input: ListRegistersFromAllDevicesRequest!) {
    ListRegistersFromAllDevices(input: $input) {
        Registers {
            ID
            DeviceID
            Name
            TagName
            Description
            ValueType
            Properties {
                Name
                Value
            }
        }
    }
}
"""


def _extract_tags(raw_registers: list) -> list[dict]:
    """Build tag dicts from raw GQL register records, skipping pydantic validation."""

    def _prop(props, name):
        for p in props or []:
            if p.get("Name") == name:
                return p.get("Value")
        return None

    tag_data = []
    for raw in raw_registers:
        props = raw.get("Properties") or []
        tag_info = {
            "tag_name": raw.get("TagName") or raw.get("Name"),
            "id": raw.get("ID"),
            "address": _prop(props, "Address") or _prop(props, "address"),
            "data_type": raw.get("ValueType") or _prop(props, "DataType"),
            "description": raw.get("Description"),
        }
        tag_data.append({k: v for k, v in tag_info.items() if v is not None})
    tag_data.sort(key=lambda x: x["tag_name"])
    return tag_data


def _parse_page_args(arguments: dict) -> tuple[int, int]:
    """Validate and return (limit, offset) pagination arguments."""
    try:
        limit = int(arguments.get("limit", _TAG_LIMIT))
        offset = int(arguments.get("offset", 0))
    except (TypeError, ValueError):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="'limit' and 'offset' must be integers",
            )
        )
    if not (1 <= limit <= _TAG_LIMIT):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=f"'limit' must be between 1 and {_TAG_LIMIT}",
            )
        )
    if offset < 0:
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message="'offset' must be >= 0")
        )
    return limit, offset


async def get_devicehub_device_tags(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Retrieves tags for a specific device or all devices, paginated.

    Counts first, then returns one page of up to `limit` tags starting at
    `offset` (GraphQL Limit/SkipCount). Response carries total_count,
    has_more, and next_offset so callers can page through any tag count.
    """
    try:
        device_name = (arguments.get("device_name") or "").strip()
        limit, offset = _parse_page_args(arguments)
        connection = get_litmus_connection(request)

        if device_name:
            # ── Single-device path ────────────────────────────────────────
            requested_device = _find_device_by_name(connection, device_name, request)
            if not requested_device:
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=f"Device '{device_name}' not found. Use get_devicehub_devices to see available devices.",
                    )
                )

            count_result = api.gql_query(
                api_paths.DH_GRAPHQL,
                {
                    "query": _COUNT_DEVICE_TAGS,
                    "variables": {"input": {"DeviceID": requested_device.id}},
                },
                connection,
            )
            total = (
                count_result.get("data", {})
                .get("ListRegisters", {})
                .get("TotalCount", 0)
            )

            # SkipCount is only sent when actually paginating, so the default
            # first-page call stays compatible with LE builds whose GraphQL
            # schema predates the field.
            list_input: dict[str, Any] = {
                "DeviceID": requested_device.id,
                "Limit": limit,
            }
            if offset:
                list_input["SkipCount"] = offset
            list_result = api.gql_query(
                api_paths.DH_GRAPHQL,
                {
                    "query": gql_queries.LIST_TAGS,
                    "variables": {"input": list_input},
                },
                connection,
            )
            raw_registers = (
                list_result.get("data", {})
                .get("ListRegisters", {})
                .get("Registers", [])
            )
            scope = f"device '{device_name}'"

        else:
            # ── All-devices path ──────────────────────────────────────────
            count_result = api.gql_query(
                api_paths.DH_GRAPHQL,
                {"query": _COUNT_ALL_TAGS, "variables": {"input": {}}},
                connection,
            )
            total = (
                count_result.get("data", {})
                .get("ListRegistersFromAllDevices", {})
                .get("TotalCount", 0)
            )

            list_input = {"Limit": limit}
            if offset:
                list_input["SkipCount"] = offset
            list_result = api.gql_query(
                api_paths.DH_GRAPHQL,
                {
                    "query": _LIST_ALL_TAGS_RAW,
                    "variables": {"input": list_input},
                },
                connection,
            )
            raw_registers = (
                list_result.get("data", {})
                .get("ListRegistersFromAllDevices", {})
                .get("Registers", [])
            )
            scope = "all devices"

        tag_data = _extract_tags(raw_registers)
        has_more = offset + len(tag_data) < total
        logger.info(
            f"Retrieved {len(tag_data)} of {total} tags for {scope} "
            f"(offset={offset}, limit={limit})"
        )

        result: dict[str, Any] = {
            "count": len(tag_data),
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "tags": tag_data,
            "tag_names": [t["tag_name"] for t in tag_data],
        }
        if has_more:
            result["next_offset"] = offset + len(tag_data)
            result["message"] = (
                f"Returned tags {offset}-{offset + len(tag_data)} of {total}. "
                f"Call again with offset={offset + len(tag_data)} for the next page."
            )
        if device_name:
            result["device_name"] = device_name

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error retrieving tags: {e}", exc_info=True)
        return format_error_response("retrieval_failed", str(e))


async def get_current_value_of_devicehub_tag(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Reads the current real-time value of a specific tag from a device.

    Requires either tag_name OR tag_id (not both).
    """
    try:
        device_name = arguments.get("device_name")
        tag_name = arguments.get("tag_name")
        tag_id = arguments.get("tag_id")

        if not device_name:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'device_name' parameter is required"
                )
            )

        if not tag_name and not tag_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="Either 'tag_name' or 'tag_id' is required. Use get_devicehub_device_tags to see available tags.",
                )
            )

        connection = get_litmus_connection(request)

        # Find device
        requested_device = _find_device_by_name(connection, device_name, request)
        if not requested_device:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Device '{device_name}' not found. Use get_devicehub_devices to see available devices.",
                )
            )

        # Find tag
        tag_list = tags.list_registers_from_single_device(
            requested_device, le_connection=connection
        )

        if tag_name:
            requested_tag = next(
                (tag for tag in tag_list if tag.tag_name == tag_name), None
            )
            identifier = f"name '{tag_name}'"
        else:
            requested_tag = next((tag for tag in tag_list if tag.id == tag_id), None)
            identifier = f"ID '{tag_id}'"

        if not requested_tag:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Tag with {identifier} not found on device '{device_name}'",
                )
            )

        # Get the output topic
        requested_value_from_topic = next(
            (
                topic.topic
                for topic in requested_tag.topics
                if topic.direction == "Output"
            ),
            None,
        )

        if not requested_value_from_topic:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"No output topic found for tag {identifier}",
                )
            )

        # Read current value
        value_data = await get_current_value_on_topic(
            topic=requested_value_from_topic, request=request
        )

        logger.info(f"Read value for {identifier} on device '{device_name}'")

        result = {
            "device_name": device_name,
            "tag_name": tag_name or requested_tag.tag_name,
            "tag_id": tag_id or requested_tag.id,
            "data": value_data,
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error reading tag value: {e}", exc_info=True)
        return format_error_response("read_failed", str(e))


def _find_device_by_name(
    connection: Any, device_name: str, request=None
) -> Optional[Any]:
    """Find a device by name, using a short-lived cache to avoid redundant API calls."""
    cache_key = ""
    if request is not None:
        cache_key = request.headers.get("EDGE_URL") or ""

    now = time.monotonic()
    if cache_key:
        cached = _device_list_cache.get(cache_key)
        if cached and now - cached[1] < _DEVICE_LIST_TTL:
            device_list = cached[0]
        else:
            device_list = devices.list_devices(le_connection=connection)
            _device_list_cache[cache_key] = (device_list, now)
    else:
        device_list = devices.list_devices(le_connection=connection)

    for device in device_list:
        if device.name == device_name:
            return device
    return None


def _build_device_info(device: Any) -> dict:
    """Build device information dictionary."""
    driver = getattr(device, "driver", None)
    if driver is not None and not isinstance(driver, str):
        driver = (
            getattr(driver, "name", None) or getattr(driver, "id", None) or str(driver)
        )
    device_info = {
        "name": device.name,
        "id": getattr(device, "id", None),
        "driver": driver,
        "metadata": getattr(device, "metadata", "unknown"),
        "description": getattr(device, "description", None),
        "properties": getattr(device, "properties", None),
    }

    device_info = {k: v for k, v in device_info.items() if v is not None}

    return device_info


def _create_device_summary(device_data: list[dict]) -> dict:
    """Create summary statistics for devices."""
    driver_counts = {}

    for device in device_data:
        driver = device.get("driver", "unknown")
        driver_counts[driver] = driver_counts.get(driver, 0) + 1

    return {
        "by_driver": driver_counts,
    }


# ── Device connection status, tag CRUD, tag status ───────────────────────────

_CONNECTION_THRESHOLD_SECONDS = 60


async def get_device_connection_status(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Check whether devices are actively publishing data by probing InfluxDB for recent records.
    Connected = data seen within threshold_seconds; stale = older; no_data = no records found.
    """
    try:
        device_name = (arguments.get("device_name") or "").strip()
        threshold = int(
            arguments.get("threshold_seconds", _CONNECTION_THRESHOLD_SECONDS)
        )

        connection = get_litmus_connection(request)
        if device_name:
            device_obj = _find_device_by_name(connection, device_name, request)
            if not device_obj:
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=f"Device '{device_name}' not found.",
                    )
                )
            device_list = [device_obj]
        else:
            device_list = devices.list_devices(le_connection=connection)

        params = get_influx_connection_params(request)
        note = _influx_connection_note(params)
        client = _make_influx_client(params)
        now_epoch = time.time()

        results = []
        for device in device_list:
            output_topics = []
            error = None
            try:
                tag_list = tags.list_registers_from_single_device(
                    device, le_connection=connection
                )
                for tag in tag_list:
                    for tp in tag.topics or []:
                        if tp.direction == "Output":
                            output_topics.append(tp.topic)
            except Exception as e:
                error = f"tag_listing_failed: {e}"
                logger.warning(
                    f"Could not list tags for device '{device.name}': {e}"
                )

            # Newest data point across all of the device's output topics.
            # SELECT last(*) is deliberately avoided: applied to multiple
            # fields, InfluxQL returns the epoch-0 timestamp instead of the
            # point's real time.
            status = "no_data"
            last_seen = None
            last_seen_topic = None
            newest_ts = None
            for output_topic in output_topics:
                try:
                    rs = client.query(
                        f'SELECT * FROM "{output_topic}" ORDER BY time DESC LIMIT 1'
                    )
                    pts = list(rs.get_points())
                    if not pts:
                        continue
                    ts_str = pts[0].get("time", "")
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts_epoch = ts.replace(tzinfo=timezone.utc).timestamp()
                    if newest_ts is None or ts_epoch > newest_ts:
                        newest_ts = ts_epoch
                        last_seen = ts_str
                        last_seen_topic = output_topic
                except Exception as e:
                    error = f"influx_query_failed: {e}"
                    logger.warning(
                        f"InfluxDB query failed for topic '{output_topic}': {e}"
                    )

            if newest_ts is not None:
                age_s = now_epoch - newest_ts
                status = "connected" if age_s <= threshold else "stale"

            result = {
                "device_name": device.name,
                "device_id": device.id,
                "status": status,
                "last_seen": last_seen,
                "checked_topic": last_seen_topic,
                "checked_topics_count": len(output_topics),
            }
            if error:
                result["error"] = error
            results.append(result)

        return format_success_response(
            _with_connection_note(
                {
                    "count": len(results),
                    "threshold_seconds": threshold,
                    "devices": results,
                },
                note,
            )
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error checking device connection status: {e}", exc_info=True)
        return format_error_response("check_failed", str(e))


def _get_register_property_defaults(device, register_name: str) -> dict:
    """Pull default values for required properties of a driver register.

    Drivers expose `supported_registers`; each register lists its properties
    with `required` and `default_value`. We fill required-with-default fields
    so callers don't need to know every driver's schema by heart.
    """
    try:
        for sr in getattr(device.driver, "supported_registers", None) or []:
            if sr.name == register_name:
                return {
                    p.name: p.default_value
                    for p in (sr.properties or [])
                    if p.required and p.default_value is not None
                }
    except Exception:
        pass
    return {}


async def create_devicehub_tag(request: Request, arguments: dict) -> list[TextContent]:
    """
    Create a new tag on a DeviceHub device.
    register_name is the driver-specific register type (e.g. 'S' for Generator,
    'HoldingRegister' for Modbus). Required driver properties (address, count,
    pollingInterval, etc.) are auto-filled from driver defaults; user-provided
    `properties` override the defaults.
    """
    try:
        device_name = (arguments.get("device_name") or "").strip()
        register_name = (arguments.get("register_name") or "").strip()
        tag_name = (arguments.get("tag_name") or "").strip()
        value_type = (arguments.get("value_type") or "").strip()
        description = arguments.get("description", "")
        user_properties = arguments.get("properties") or {}

        for field, val in [
            ("device_name", device_name),
            ("register_name", register_name),
            ("tag_name", tag_name),
            ("value_type", value_type),
        ]:
            if not val:
                raise McpError(
                    ErrorData(code=INVALID_PARAMS, message=f"'{field}' is required")
                )

        connection = get_litmus_connection(request)
        device = _find_device_by_name(connection, device_name, request)
        if not device:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message=f"Device '{device_name}' not found."
                )
            )

        defaults = _get_register_property_defaults(device, register_name)
        properties = {**defaults, **user_properties}

        tag = Tag.model_validate(
            {
                "DeviceID": device,
                "name": register_name,
                "tag_name": tag_name,
                "value_type": value_type,
                "description": description,
                "properties": properties,
            },
            context={"le_connection": connection},
        )

        created = tags.create_tags([tag], le_connection=connection)
        result_tag = created[0] if created else tag

        return format_success_response(
            {
                "tag_id": result_tag.id,
                "tag_name": result_tag.tag_name,
                "device_name": device_name,
                "register_name": result_tag.name,
                "value_type": result_tag.value_type,
            }
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error creating tag: {e}", exc_info=True)
        return format_error_response("creation_failed", str(e))


async def update_devicehub_tag(request: Request, arguments: dict) -> list[TextContent]:
    """Update mutable fields of an existing tag (tag_name, description, properties)."""
    try:
        device_name = (arguments.get("device_name") or "").strip()
        tag_name = (arguments.get("tag_name") or "").strip()

        if not device_name:
            raise McpError(
                ErrorData(code=INVALID_PARAMS, message="'device_name' is required")
            )
        if not tag_name:
            raise McpError(
                ErrorData(code=INVALID_PARAMS, message="'tag_name' is required")
            )

        connection = get_litmus_connection(request)
        device = _find_device_by_name(connection, device_name, request)
        if not device:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message=f"Device '{device_name}' not found."
                )
            )

        tag_list = tags.list_registers_from_single_device(
            device, le_connection=connection
        )
        existing = next((t for t in tag_list if t.tag_name == tag_name), None)
        if not existing:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Tag '{tag_name}' not found on device '{device_name}'.",
                )
            )

        updates = {
            k: arguments[k]
            for k in ("new_tag_name", "description", "properties")
            if k in arguments
        }
        if not updates:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="Provide at least one of: new_tag_name, description, properties",
                )
            )

        updated_tag = Tag.model_validate(
            {
                "ID": existing.id,
                "DeviceID": device,
                "name": existing.name,
                "tag_name": updates.get("new_tag_name", existing.tag_name),
                "description": updates.get("description", existing.description),
                "value_type": existing.value_type,
                "properties": updates.get("properties", existing.properties),
                "PublishCoV": existing.publish_cov,
                "MetaData": existing.metadata,
            },
            context={"le_connection": connection, "skip_property_validation": True},
        )

        result = tags.update_tags([updated_tag], le_connection=connection)
        result_tag = result[0] if result else updated_tag

        return format_success_response(
            {
                "tag_id": result_tag.id,
                "tag_name": result_tag.tag_name,
                "device_name": device_name,
            }
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error updating tag: {e}", exc_info=True)
        return format_error_response("update_failed", str(e))


async def delete_devicehub_tag(request: Request, arguments: dict) -> list[TextContent]:
    """Delete a tag from a DeviceHub device. Destructive — cannot be undone."""
    try:
        device_name = (arguments.get("device_name") or "").strip()
        tag_name = (arguments.get("tag_name") or "").strip()

        if not device_name:
            raise McpError(
                ErrorData(code=INVALID_PARAMS, message="'device_name' is required")
            )
        if not tag_name:
            raise McpError(
                ErrorData(code=INVALID_PARAMS, message="'tag_name' is required")
            )

        connection = get_litmus_connection(request)
        device = _find_device_by_name(connection, device_name, request)
        if not device:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message=f"Device '{device_name}' not found."
                )
            )

        tag_list = tags.list_registers_from_single_device(
            device, le_connection=connection
        )
        tag = next((t for t in tag_list if t.tag_name == tag_name), None)
        if not tag:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Tag '{tag_name}' not found on device '{device_name}'.",
                )
            )

        tags.delete_tag(tag, le_connection=connection)

        return format_success_response(
            {
                "deleted": True,
                "tag_name": tag_name,
                "tag_id": tag.id,
                "device_name": device_name,
            }
        )

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error deleting tag: {e}", exc_info=True)
        return format_error_response("deletion_failed", str(e))


# Tag status is backed by the litmus-cli Go binary: the Python SDK path
# requires every tag on a device to pass strict pydantic validation before a
# status can be fetched, so a single quirky tag silently dropped a whole
# device from the results.

_STATUS_FANOUT_CONCURRENCY = 4


async def _cli_device_tag_statuses(
    request: Request, device_id: str
) -> tuple[dict, list]:
    """Return ({tag_id: tag_name}, [{'ID', 'State'}...]) for one device via
    litmus-cli."""
    cli_tags = (
        await run_cli_function(
            request,
            "le.devicehub.ListDeviceTags",
            {"deviceID": device_id, "limit": _TAG_LIMIT},
        )
        or []
    )
    tag_map = {
        t.get("ID"): t.get("TagName") or t.get("Name")
        for t in cli_tags
        if isinstance(t, dict) and t.get("ID")
    }
    if not tag_map:
        return {}, []
    states = (
        await run_cli_function(
            request,
            "le.devicehub.TagStatus",
            {"deviceID": device_id, "tagIDs": list(tag_map.keys())},
        )
        or []
    )
    return tag_map, [s for s in states if isinstance(s, dict)]


async def get_tag_status(request: Request, arguments: dict) -> list[TextContent]:
    """Get OK/ERROR status for tags on a device. Optionally filter to a single tag."""
    try:
        device_name = (arguments.get("device_name") or "").strip()
        filter_tag = (arguments.get("tag_name") or "").strip()

        if not device_name:
            raise McpError(
                ErrorData(code=INVALID_PARAMS, message="'device_name' is required")
            )

        connection = get_litmus_connection(request)
        device = _find_device_by_name(connection, device_name, request)
        if not device:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message=f"Device '{device_name}' not found."
                )
            )

        tag_map, states = await _cli_device_tag_statuses(request, device.id)

        statuses = [
            {**s, "tag_name": tag_map.get(s.get("ID", ""), "unknown")}
            for s in states
        ]
        if filter_tag:
            statuses = [s for s in statuses if s.get("tag_name") == filter_tag]
            if not statuses:
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=f"Tag '{filter_tag}' not found on device '{device_name}'.",
                    )
                )

        return format_success_response(
            {
                "device_name": device_name,
                "count": len(statuses),
                "statuses": statuses,
            }
        )

    except McpError:
        raise
    except CLIFunctionError as e:
        return format_error_response("status_failed", str(e))
    except Exception as e:
        logger.error(f"Error getting tag status: {e}", exc_info=True)
        return format_error_response("status_failed", str(e))


async def get_all_tags_status(request: Request, arguments: dict) -> list[TextContent]:
    """
    Get tag status across all devices. Defaults to returning only non-OK tags.
    Pass filter_status='' to see all statuses.
    """
    try:
        filter_state = (arguments.get("filter_status", "not_ok") or "").strip().upper()
        # The LE RegisterState enum is OK/Failed/Unknown; accept the commonly
        # guessed 'ERROR' as an alias for 'Failed'.
        if filter_state == "ERROR":
            filter_state = "FAILED"

        device_list = (
            await run_cli_function(request, "le.devicehub.ListDevices", {}) or []
        )
        device_list = [d for d in device_list if isinstance(d, dict) and d.get("ID")]

        semaphore = asyncio.Semaphore(_STATUS_FANOUT_CONCURRENCY)
        device_errors = []

        async def _one(device: dict) -> list[dict]:
            async with semaphore:
                try:
                    tag_map, states = await _cli_device_tag_statuses(
                        request, device["ID"]
                    )
                except Exception as ex:
                    logger.warning(
                        f"Could not get tag status for device '{device.get('Name')}': {ex}"
                    )
                    device_errors.append(
                        {"device_name": device.get("Name"), "error": str(ex)}
                    )
                    return []
            return [
                {
                    **s,
                    "tag_name": tag_map.get(s.get("ID", ""), "unknown"),
                    "device_name": device.get("Name"),
                    "device_id": device["ID"],
                }
                for s in states
            ]

        per_device = await asyncio.gather(*[_one(d) for d in device_list])
        all_statuses = [s for group in per_device for s in group]

        if filter_state == "NOT_OK":
            all_statuses = [
                s for s in all_statuses if s.get("State", "OK").upper() != "OK"
            ]
        elif filter_state:
            all_statuses = [
                s for s in all_statuses if s.get("State", "").upper() == filter_state
            ]

        result: dict[str, Any] = {
            "count": len(all_statuses),
            "filter_status": filter_state or None,
            "statuses": all_statuses,
            "devices_checked": len(device_list),
        }
        if device_errors:
            result["device_errors"] = device_errors

        return format_success_response(result)

    except McpError:
        raise
    except CLIFunctionError as e:
        return format_error_response("status_failed", str(e))
    except Exception as e:
        logger.error(f"Error getting all tag statuses: {e}", exc_info=True)
        return format_error_response("status_failed", str(e))


TOOLS = [
    {
        "name": "get_litmusedge_driver_list",
        "category": "devicehub.drivers",
        "annotations": ToolAnnotations(title="List DeviceHub Drivers", readOnlyHint=True),
        "description": (
            "Retrieves all available drivers supported by Litmus Edge DeviceHub. "
            "Returns a list of supported industrial protocols and device drivers "
            "(e.g., ModbusTCP, OPCUA, BACnet, MQTT). Use this before creating new "
            "devices to see what drivers are available."
        ),
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": get_litmusedge_driver_list,
    },
    {
        "name": "get_devicehub_devices",
        "category": "devicehub.devices",
        "annotations": ToolAnnotations(title="List DeviceHub Devices", readOnlyHint=True),
        "description": (
            "Retrieves all configured devices in the DeviceHub module on Litmus Edge. "
            "Returns detailed information about each device including name, driver type, "
            "connection settings, and status. Supports filtering by driver."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "filter_by_driver": {
                    "type": "string",
                    "description": "Optional: Filter devices by driver name (e.g., 'ModbusTCP')",
                },
            },
            "required": [],
        },
        "handler": get_devicehub_devices,
    },
    {
        "name": "create_devicehub_device",
        "category": "devicehub.devices",
        "annotations": ToolAnnotations(title="Create DeviceHub Device", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Creates a new device in DeviceHub with specified driver and default configuration. "
            "IMPORTANT: This only creates the device with default settings. You'll need to: "
            "1) Update connection properties (IP, port, slave ID, etc.), "
            "2) Configure tags/registers, and 3) Enable the device."
        ),
        "schema": {
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
        "handler": create_devicehub_device,
    },
    {
        "name": "get_device_connection_status",
        "category": "devicehub.devices",
        "annotations": ToolAnnotations(title="Get Device Connection Status", readOnlyHint=True),
        "description": (
            "Checks whether DeviceHub devices are actively publishing data by probing InfluxDB "
            "for recent records. Returns connected/stale/no_data per device. "
            "Use this to diagnose disconnected or silent devices."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Specific device to check (omit for all devices)",
                },
                "threshold_seconds": {
                    "type": "integer",
                    "description": "Age threshold in seconds to consider connected (default 60)",
                    "default": 60,
                },
            },
            "required": [],
        },
        "handler": get_device_connection_status,
    },
    {
        "name": "get_devicehub_device_tags",
        "category": "devicehub.tags",
        "annotations": ToolAnnotations(title="List Device Tags", readOnlyHint=True),
        "description": (
            "Retrieves tags (data points/registers) with their configuration, "
            "paginated. If device_name is provided, returns tags for that device "
            "only; otherwise tags across ALL devices. Returns up to `limit` tags "
            "per call starting at `offset`, plus total_count/has_more/next_offset. "
            "When has_more is true, call again with offset=next_offset to fetch "
            "the next page - any total tag count can be paged through."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Name of the device to filter by (omit to query all devices)",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Page size, 1-{_TAG_LIMIT} (default {_TAG_LIMIT})",
                    "default": _TAG_LIMIT,
                },
                "offset": {
                    "type": "integer",
                    "description": "Number of tags to skip; use next_offset from the previous page (default 0)",
                    "default": 0,
                },
            },
            "required": [],
        },
        "handler": get_devicehub_device_tags,
    },
    {
        "name": "get_current_value_of_devicehub_tag",
        "category": "devicehub.tags",
        "annotations": ToolAnnotations(title="Get Tag Current Value", readOnlyHint=True),
        "description": (
            "Reads the current real-time value of a specific tag from a device. "
            "Returns the value along with timestamp and quality. "
            "You must provide either tag_name OR tag_id (not both)."
        ),
        "schema": {
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
        "handler": get_current_value_of_devicehub_tag,
    },
    {
        "name": "create_devicehub_tag",
        "category": "devicehub.tags",
        "annotations": ToolAnnotations(title="Create Device Tag", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Creates a new tag (register) on a DeviceHub device. "
            "register_name is the driver-specific register type (e.g. 'S' for Generator, "
            "'HoldingRegister' for Modbus). Required driver properties (address, count, "
            "pollingInterval, etc.) auto-fill from driver defaults; pass `properties` to "
            "override individual fields. Use get_litmusedge_driver_list to find drivers "
            "and get_devicehub_device_tags to see existing tags."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Name of the device to add the tag to",
                },
                "register_name": {
                    "type": "string",
                    "description": "Driver register type (e.g. 'S' for Generator, 'HoldingRegister' for Modbus)",
                },
                "tag_name": {
                    "type": "string",
                    "description": "Display name for the tag",
                },
                "value_type": {
                    "type": "string",
                    "description": "Data type (e.g. 'float64', 'int64', 'bit', 'string')",
                },
                "description": {
                    "type": "string",
                    "description": "Optional description",
                },
                "properties": {
                    "type": "object",
                    "description": 'Optional driver-specific overrides (e.g. {"address": "5", "pollingInterval": "500"}). Missing required fields are filled from driver defaults.',
                },
            },
            "required": ["device_name", "register_name", "tag_name", "value_type"],
        },
        "handler": create_devicehub_tag,
    },
    {
        "name": "update_devicehub_tag",
        "category": "devicehub.tags",
        "annotations": ToolAnnotations(title="Update Device Tag", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Updates mutable fields of an existing DeviceHub tag: display name, description, or properties. "
            "The device and tag must already exist. Use get_devicehub_device_tags to find tag names."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Name of the device owning the tag",
                },
                "tag_name": {
                    "type": "string",
                    "description": "Current display name of the tag to update",
                },
                "new_tag_name": {
                    "type": "string",
                    "description": "New display name (optional)",
                },
                "description": {
                    "type": "string",
                    "description": "New description (optional)",
                },
                "properties": {
                    "type": "object",
                    "description": "New properties dict (optional)",
                },
            },
            "required": ["device_name", "tag_name"],
        },
        "handler": update_devicehub_tag,
    },
    {
        "name": "delete_devicehub_tag",
        "category": "devicehub.tags",
        "annotations": ToolAnnotations(title="Delete Device Tag", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Deletes a tag from a DeviceHub device. This is destructive and cannot be undone. "
            "Use get_devicehub_device_tags to confirm the tag name before deleting."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Name of the device owning the tag",
                },
                "tag_name": {
                    "type": "string",
                    "description": "Display name of the tag to delete",
                },
            },
            "required": ["device_name", "tag_name"],
        },
        "handler": delete_devicehub_tag,
    },
    {
        "name": "get_tag_status",
        "category": "devicehub.tags",
        "annotations": ToolAnnotations(title="Get Tag Status", readOnlyHint=True),
        "description": (
            "Returns the runtime state for tags on a specific device. "
            "State is one of OK, Failed, or Unknown (LE RegisterState enum). "
            "Optionally filter to a single tag by name. "
            "Use this to diagnose which tags are failing on a device."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Name of the device to check",
                },
                "tag_name": {
                    "type": "string",
                    "description": "Optional: check a single tag by name",
                },
            },
            "required": ["device_name"],
        },
        "handler": get_tag_status,
    },
    {
        "name": "get_all_tags_status",
        "category": "devicehub.tags",
        "annotations": ToolAnnotations(title="Get All Tags Status", readOnlyHint=True),
        "description": (
            "Returns tag status across ALL devices. Tag state is one of OK, "
            "Failed, or Unknown (LE RegisterState enum). Defaults to returning "
            "only non-OK tags so the LLM sees actionable issues first. Pass "
            "filter_status='' to see all. Use get_tag_status for a single device."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "filter_status": {
                    "type": "string",
                    "description": "Filter by state: 'not_ok' (default), 'OK', 'Failed', 'Unknown', or '' for all",
                    "default": "not_ok",
                },
            },
            "required": [],
        },
        "handler": get_all_tags_status,
    },
]
