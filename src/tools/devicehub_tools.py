import time
from datetime import datetime, timezone
from typing import Optional, Any
from config import logger
from utils.auth import get_litmus_connection, get_influx_connection_params
from utils.formatting import format_success_response, format_error_response
from .data_tools import get_current_value_on_topic, _make_influx_client

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR
from mcp.types import TextContent
from starlette.requests import Request
from litmussdk.devicehub import devices, tags
from litmussdk.devicehub.tags._models import Tag
from litmussdk.devicehub.drivers import list_all_drivers
from litmussdk.utils import api, api_paths, gql_queries

# Short-lived cache for the device list, keyed by EDGE_URL.
# Avoids redundant API round-trips when the LLM calls multiple device tools
# back-to-back within the same conversation turn.
_device_list_cache: dict[str, tuple[list, float]] = {}
_DEVICE_LIST_TTL = 10  # seconds


async def get_litmusedge_driver_list(request: Request) -> list[TextContent]:
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


async def get_devicehub_device_tags(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Retrieves tags for a specific device or all devices.

    Always counts first; refuses to list if total exceeds _TAG_LIMIT.
    """
    try:
        device_name = (arguments.get("device_name") or "").strip()
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
                {"query": _COUNT_DEVICE_TAGS,
                 "variables": {"input": {"DeviceID": requested_device.id}}},
                connection,
            )
            total = (
                count_result.get("data", {})
                .get("ListRegisters", {})
                .get("TotalCount", 0)
            )
            if total > _TAG_LIMIT:
                return format_success_response({
                    "device_name": device_name,
                    "total_count": total,
                    "message": (
                        f"Device '{device_name}' has {total} tags, which exceeds "
                        f"the limit of {_TAG_LIMIT}. Cannot return the full list."
                    ),
                })

            list_result = api.gql_query(
                api_paths.DH_GRAPHQL,
                {"query": gql_queries.LIST_TAGS,
                 "variables": {"input": {"DeviceID": requested_device.id, "Limit": _TAG_LIMIT}}},
                connection,
            )
            raw_registers = (
                list_result.get("data", {}).get("ListRegisters", {}).get("Registers", [])
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
            if total > _TAG_LIMIT:
                return format_success_response({
                    "total_count": total,
                    "message": (
                        f"There are {total} tags across all devices, which exceeds "
                        f"the limit of {_TAG_LIMIT}. Specify a device_name to narrow the query."
                    ),
                })

            list_result = api.gql_query(
                api_paths.DH_GRAPHQL,
                {"query": _LIST_ALL_TAGS_RAW, "variables": {"input": {"Limit": _TAG_LIMIT}}},
                connection,
            )
            raw_registers = (
                list_result.get("data", {})
                .get("ListRegistersFromAllDevices", {})
                .get("Registers", [])
            )
            scope = "all devices"

        tag_data = _extract_tags(raw_registers)
        logger.info(f"Retrieved {len(tag_data)} tags for {scope}")

        result: dict[str, Any] = {
            "count": len(tag_data),
            "tags": tag_data,
            "tag_names": [t["tag_name"] for t in tag_data],
        }
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
        tag_list = tags.list_registers_from_single_device(requested_device)

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
        driver = getattr(driver, "name", None) or getattr(driver, "id", None) or str(driver)
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
        threshold = int(arguments.get("threshold_seconds", _CONNECTION_THRESHOLD_SECONDS))

        connection = get_litmus_connection(request)
        if device_name:
            device_obj = _find_device_by_name(connection, device_name, request)
            if not device_obj:
                raise McpError(ErrorData(code=INVALID_PARAMS,
                    message=f"Device '{device_name}' not found."))
            device_list = [device_obj]
        else:
            device_list = devices.list_devices(le_connection=connection)

        params = get_influx_connection_params(request)
        client = _make_influx_client(params)
        now_epoch = time.time()

        results = []
        for device in device_list:
            output_topic = None
            try:
                tag_list = tags.list_registers_from_single_device(device, le_connection=connection)
                for tag in tag_list:
                    for tp in (tag.topics or []):
                        if tp.direction == "Output":
                            output_topic = tp.topic
                            break
                    if output_topic:
                        break
            except Exception:
                pass

            status = "no_data"
            last_seen = None
            if output_topic:
                try:
                    rs = client.query(f'SELECT last(*) FROM "{output_topic}"')
                    pts = list(rs.get_points())
                    if pts:
                        ts_str = pts[0].get("time", "")
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        last_seen = ts_str
                        age_s = now_epoch - ts.replace(tzinfo=timezone.utc).timestamp()
                        status = "connected" if age_s <= threshold else "stale"
                except Exception:
                    pass

            results.append({
                "device_name": device.name,
                "device_id": device.id,
                "status": status,
                "last_seen": last_seen,
                "checked_topic": output_topic,
            })

        return format_success_response({
            "count": len(results),
            "threshold_seconds": threshold,
            "devices": results,
        })

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
        for sr in (getattr(device.driver, "supported_registers", None) or []):
            if sr.name == register_name:
                return {
                    p.name: p.default_value
                    for p in (sr.properties or [])
                    if p.required and p.default_value is not None
                }
    except Exception:
        pass
    return {}


async def create_devicehub_tag(
    request: Request, arguments: dict
) -> list[TextContent]:
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

        for field, val in [("device_name", device_name), ("register_name", register_name),
                           ("tag_name", tag_name), ("value_type", value_type)]:
            if not val:
                raise McpError(ErrorData(code=INVALID_PARAMS, message=f"'{field}' is required"))

        connection = get_litmus_connection(request)
        device = _find_device_by_name(connection, device_name, request)
        if not device:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Device '{device_name}' not found."))

        defaults = _get_register_property_defaults(device, register_name)
        properties = {**defaults, **user_properties}

        tag = Tag.model_validate({
            "DeviceID": device,
            "name": register_name,
            "tag_name": tag_name,
            "value_type": value_type,
            "description": description,
            "properties": properties,
        }, context={"le_connection": connection})

        created = tags.create_tags([tag], le_connection=connection)
        result_tag = created[0] if created else tag

        return format_success_response({
            "tag_id": result_tag.id,
            "tag_name": result_tag.tag_name,
            "device_name": device_name,
            "register_name": result_tag.name,
            "value_type": result_tag.value_type,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error creating tag: {e}", exc_info=True)
        return format_error_response("creation_failed", str(e))


async def update_devicehub_tag(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Update mutable fields of an existing tag (tag_name, description, properties)."""
    try:
        device_name = (arguments.get("device_name") or "").strip()
        tag_name = (arguments.get("tag_name") or "").strip()

        if not device_name:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'device_name' is required"))
        if not tag_name:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'tag_name' is required"))

        connection = get_litmus_connection(request)
        device = _find_device_by_name(connection, device_name, request)
        if not device:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Device '{device_name}' not found."))

        tag_list = tags.list_registers_from_single_device(device, le_connection=connection)
        existing = next((t for t in tag_list if t.tag_name == tag_name), None)
        if not existing:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Tag '{tag_name}' not found on device '{device_name}'."))

        updates = {k: arguments[k] for k in ("new_tag_name", "description", "properties")
                   if k in arguments}
        if not updates:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message="Provide at least one of: new_tag_name, description, properties"))

        updated_tag = Tag.model_validate({
            "ID": existing.id,
            "DeviceID": device,
            "name": existing.name,
            "tag_name": updates.get("new_tag_name", existing.tag_name),
            "description": updates.get("description", existing.description),
            "value_type": existing.value_type,
            "properties": updates.get("properties", existing.properties),
            "PublishCoV": existing.publish_cov,
            "MetaData": existing.metadata,
        }, context={"le_connection": connection, "skip_property_validation": True})

        result = tags.update_tags([updated_tag], le_connection=connection)
        result_tag = result[0] if result else updated_tag

        return format_success_response({
            "tag_id": result_tag.id,
            "tag_name": result_tag.tag_name,
            "device_name": device_name,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error updating tag: {e}", exc_info=True)
        return format_error_response("update_failed", str(e))


async def delete_devicehub_tag(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Delete a tag from a DeviceHub device. Destructive — cannot be undone."""
    try:
        device_name = (arguments.get("device_name") or "").strip()
        tag_name = (arguments.get("tag_name") or "").strip()

        if not device_name:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'device_name' is required"))
        if not tag_name:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'tag_name' is required"))

        connection = get_litmus_connection(request)
        device = _find_device_by_name(connection, device_name, request)
        if not device:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Device '{device_name}' not found."))

        tag_list = tags.list_registers_from_single_device(device, le_connection=connection)
        tag = next((t for t in tag_list if t.tag_name == tag_name), None)
        if not tag:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Tag '{tag_name}' not found on device '{device_name}'."))

        tags.delete_tag(tag, le_connection=connection)

        return format_success_response({
            "deleted": True,
            "tag_name": tag_name,
            "tag_id": tag.id,
            "device_name": device_name,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error deleting tag: {e}", exc_info=True)
        return format_error_response("deletion_failed", str(e))


async def get_tag_status(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Get OK/ERROR status for tags on a device. Optionally filter to a single tag."""
    try:
        device_name = (arguments.get("device_name") or "").strip()
        filter_tag = (arguments.get("tag_name") or "").strip()

        if not device_name:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'device_name' is required"))

        connection = get_litmus_connection(request)
        device = _find_device_by_name(connection, device_name, request)
        if not device:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Device '{device_name}' not found."))

        tag_list = tags.list_registers_from_single_device(device, le_connection=connection)
        if filter_tag:
            tag_list = [t for t in tag_list if t.tag_name == filter_tag]
            if not tag_list:
                raise McpError(ErrorData(code=INVALID_PARAMS,
                    message=f"Tag '{filter_tag}' not found on device '{device_name}'."))

        if not tag_list:
            return format_success_response({"device_name": device_name, "count": 0, "statuses": []})

        raw_statuses = tags.tag_status(device, tag_list, le_connection=connection)
        tag_map = {t.id: t.tag_name for t in tag_list}
        statuses = [
            {**s, "tag_name": tag_map.get(s.get("ID", ""), "unknown")}
            for s in raw_statuses
        ]

        return format_success_response({
            "device_name": device_name,
            "count": len(statuses),
            "statuses": statuses,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting tag status: {e}", exc_info=True)
        return format_error_response("status_failed", str(e))


async def get_all_tags_status(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Get tag status across all devices. Defaults to returning only non-OK tags.
    Pass filter_status='' to see all statuses.
    """
    try:
        filter_state = (arguments.get("filter_status", "not_ok") or "").strip().upper()

        connection = get_litmus_connection(request)
        device_list = devices.list_devices(le_connection=connection)

        all_statuses = []
        for device in device_list:
            try:
                tag_list = tags.list_registers_from_single_device(device, le_connection=connection)
                if not tag_list:
                    continue
                raw = tags.tag_status(device, tag_list, le_connection=connection)
                tag_map = {t.id: t.tag_name for t in tag_list}
                for s in raw:
                    all_statuses.append({
                        **s,
                        "tag_name": tag_map.get(s.get("ID", ""), "unknown"),
                        "device_name": device.name,
                        "device_id": device.id,
                    })
            except Exception as ex:
                logger.warning(f"Could not get tag status for device '{device.name}': {ex}")

        if filter_state == "NOT_OK":
            all_statuses = [s for s in all_statuses if s.get("State", "OK").upper() != "OK"]
        elif filter_state:
            all_statuses = [s for s in all_statuses
                            if s.get("State", "").upper() == filter_state]

        return format_success_response({
            "count": len(all_statuses),
            "filter_status": filter_state or None,
            "statuses": all_statuses,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting all tag statuses: {e}", exc_info=True)
        return format_error_response("status_failed", str(e))
