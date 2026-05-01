import asyncio
import difflib
import re
import nats
import json
import pandas as pd
from typing import Optional
from datetime import datetime

from config import logger, ssl_config
from config import NATS_PORT, NATS_SOURCE

from utils.formatting import format_success_response, format_error_response
from utils.auth import get_nats_connection_params, get_influx_connection_params, get_litmus_connection

from numpy import zeros
from starlette.requests import Request
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR
from mcp.types import TextContent

import influxdb
from litmussdk.devicehub import devices as dh_devices, tags as dh_tags

INFLUXDB_AVAILABLE = True

# How long to wait for a NATS message before giving up
NATS_TIMEOUT = 30  # seconds


async def get_current_value_on_topic(
    topic: str,
    nats_source: Optional[str] = None,
    nats_port: Optional[str] = None,
    request: Optional[Request] = None,
) -> dict:
    """
    Subscribes to a NATS topic and retrieves the next published message.
    """
    # Get connection parameters from auth function if request is provided
    use_tls = True
    if request:
        try:
            params = get_nats_connection_params(request)
            nats_source = params["nats_source"]
            nats_port = params["nats_port"]
            nats_user = params.get("nats_user")
            nats_password = params.get("nats_password")
            use_tls = params.get("use_tls", True)
        except McpError:
            # Fall back to provided parameters or config defaults
            nats_source = nats_source or NATS_SOURCE
            nats_port = nats_port or NATS_PORT
            nats_user = None
            nats_password = None
            logger.warning(
                "NATS params missing from request headers, using config defaults: %s:%s",
                nats_source, nats_port,
            )
    else:
        # Use provided parameters or config defaults
        nats_source = nats_source or NATS_SOURCE
        nats_port = nats_port or NATS_PORT
        nats_user = None
        nats_password = None

    stop_event = asyncio.Event()
    final_message = await _nc_single_topic(
        nats_source,
        nats_port,
        topic,
        stop_event,
        nats_user=nats_user,
        nats_password=nats_password,
        use_tls=use_tls,
    )
    return final_message


async def get_current_value_on_topic_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Gets the current value from a NATS topic.

    Waits for the next message published to the topic and returns it.
    """
    try:
        topic = arguments.get("topic")

        if not topic:
            raise McpError(
                ErrorData(code=INVALID_PARAMS, message="'topic' parameter is required")
            )

        message = await get_current_value_on_topic(topic=topic, request=request)

        logger.info(f"Retrieved value from topic: {topic}")

        result = {
            "topic": topic,
            "data": message,
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting value from topic: {e}", exc_info=True)
        return format_error_response("retrieval_failed", str(e))


async def get_multiple_values_from_topic_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Collects multiple sequential values from a NATS topic for trend analysis.

    WARNING: This function blocks until num_samples messages are received.
    """
    try:
        topic = arguments.get("topic")
        num_samples = arguments.get("num_samples", 10)
        nats_source = arguments.get("nats_source")
        nats_port = arguments.get("nats_port")

        if not topic:
            raise McpError(
                ErrorData(code=INVALID_PARAMS, message="'topic' parameter is required")
            )

        if num_samples > 100:
            logger.warning(f"num_samples={num_samples} is high, capping at 100")
            num_samples = 100

        # Get connection parameters from auth function
        use_tls = True
        try:
            params = get_nats_connection_params(request)
            nats_source = params["nats_source"]
            nats_port = params["nats_port"]
            nats_user = params.get("nats_user")
            nats_password = params.get("nats_password")
            use_tls = params.get("use_tls", True)
        except McpError:
            # Fall back to provided parameters or config defaults
            nats_source = nats_source or NATS_SOURCE
            nats_port = nats_port or NATS_PORT
            nats_user = None
            nats_password = None
            logger.warning(
                "NATS params missing from request headers, using config defaults: %s:%s",
                nats_source, nats_port,
            )

        stop_event = asyncio.Event()

        output = await _collect_multiple_values_from_topic(
            nats_source,
            nats_port,
            topic,
            stop_event,
            num_samples,
            nats_user=nats_user,
            nats_password=nats_password,
            use_tls=use_tls,
        )

        logger.info(f"Collected {num_samples} samples from topic: {topic}")

        result = {
            "topic": topic,
            "num_samples": num_samples,
            "values": output["values"].tolist(),  # Convert numpy array to list
            "timestamps": output["humanTimestamps"],
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error collecting values from topic: {e}", exc_info=True)
        return format_error_response("collection_failed", str(e))


def _get_connect_options(nats_source, nats_port, nats_user, nats_password, use_tls=True):
    connect_options = {
        "servers": [f"nats://{nats_source}:{nats_port}"],
        "allow_reconnect": False,  # per-call connections; no background reconnect loop
    }

    if use_tls:
        connect_options["tls"] = ssl_config()

    if nats_user and nats_password:
        connect_options["user"] = nats_user
        connect_options["password"] = nats_password

    return connect_options


async def _nc_single_topic(
    nats_source: str,
    nats_port: str,
    nats_subscription_topic: str,
    stop_event: asyncio.Event,
    nats_user: Optional[str] = None,
    nats_password: Optional[str] = None,
    use_tls: bool = True,
) -> dict:
    """
    Subscribe to a single topic and return a single message.
    """

    connect_options = _get_connect_options(
        nats_source, nats_port, nats_user, nats_password, use_tls=use_tls
    )
    nc = await nats.connect(**connect_options)

    result_message = {}

    async def message_handler(msg):
        nonlocal result_message
        if result_message:
            stop_event.set()
            return

        data = msg.data.decode()
        message = json.loads(data)
        result_message = message
        stop_event.set()

    try:
        await nc.subscribe(nats_subscription_topic, cb=message_handler)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=NATS_TIMEOUT)
        except asyncio.TimeoutError:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=(
                        f"Timed out waiting for a message on topic "
                        f"'{nats_subscription_topic}' after {NATS_TIMEOUT}s. "
                        "Check that the topic is active and publishing data."
                    ),
                )
            )
    finally:
        try:
            await nc.drain()
        except Exception:
            await nc.close()

    return result_message


async def _collect_multiple_values_from_topic(
    nats_source: str,
    nats_port: str,
    topic: str,
    stop_event: asyncio.Event,
    num_samples: int = 10,
    nats_user: Optional[str] = None,
    nats_password: Optional[str] = None,
    use_tls: bool = True,
) -> dict:
    """
    Collect multiple values from a topic for plotting or analysis.
    """
    connect_options = _get_connect_options(
        nats_source, nats_port, nats_user, nats_password, use_tls=use_tls
    )
    nc = await nats.connect(**connect_options)

    results = {
        "humanTimestamps": ["" for _ in range(num_samples)],
        "values": zeros(num_samples),
    }
    counter = 0

    async def message_handler(msg):
        nonlocal counter, results
        data = msg.data.decode()
        payload = json.loads(data)

        value = payload.get("value")
        timestamp = payload.get("timestamp")

        if value is None or timestamp is None:
            return

        human_ts = str(datetime.fromtimestamp(timestamp / 1000))

        if counter < num_samples:
            results["values"][counter] = value
            results["humanTimestamps"][counter] = human_ts
            counter += 1
            if counter >= num_samples:
                stop_event.set()
        else:
            stop_event.set()

    try:
        await nc.subscribe(topic, cb=message_handler)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=NATS_TIMEOUT)
        except asyncio.TimeoutError:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=(
                        f"Timed out collecting {num_samples} samples from topic "
                        f"'{topic}' after {NATS_TIMEOUT}s "
                        f"(received {counter}/{num_samples}). "
                        "Check that the topic is active and publishing data."
                    ),
                )
            )
    finally:
        try:
            await nc.drain()
        except Exception:
            await nc.close()

    return results


async def get_historical_data_from_influxdb_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Queries historical time-series data from InfluxDB.

    User provides the measurement name and how much data they want (time range).
    """
    logger.info("Trying")
    params = get_influx_connection_params(request)
    influx_host = params["INFLUX_HOST"]
    influx_port = params["INFLUX_PORT"]
    influx_username = params["INFLUX_USERNAME"]
    influx_password = params["INFLUX_PASSWORD"]
    influx_db_name = params["INFLUX_DB_NAME"]
    try:
        if not INFLUXDB_AVAILABLE:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="InfluxDB library not installed. Install with: pip install influxdb",
                )
            )
        logger.info("Influx query")
        # Extract parameters
        measurement = arguments.get("measurement")
        time_range = arguments.get("time_range", "1h")

        # Validate inputs
        if not measurement:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="'measurement' parameter is required",
                )
            )

        # Validate inputs before interpolating into the query string
        if not re.fullmatch(r'[\w][\w\-\.]*', measurement):
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Invalid measurement name '{measurement}'. Only alphanumeric characters, underscores, hyphens, and dots are allowed.",
                )
            )
        if not re.fullmatch(r'\d+(ms|[usmhdw])', time_range):
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Invalid time_range '{time_range}'. Expected InfluxDB duration format, e.g. '1h', '30m', '7d'.",
                )
            )

        # Build query - select all fields from the measurement
        query = f'SELECT * FROM "{measurement}" WHERE time > now() - {time_range}'

        logger.info(f"Executing InfluxDB query: {query}")

        # Create InfluxDB client
        influx_client = influxdb.InfluxDBClient(
            host=influx_host,
            port=influx_port,
            username=influx_username,
            password=influx_password,
            database=influx_db_name,
            ssl=False,
        )

        # Execute query
        result = influx_client.query(query, chunked=True, chunk_size=10000)
        points = list(result.get_points())

        if not points:
            logger.warning(f"No data returned from InfluxDB for query: {query}")
            return format_success_response(
                {
                    "query": query,
                    "data": [],
                    "count": 0,
                    "message": "No data found for the specified query",
                }
            )

        # Convert to DataFrame for easier manipulation
        df = pd.DataFrame(points)

        # Convert DataFrame to list of dictionaries for JSON serialization
        data_records = df.to_dict(orient="records")

        logger.info(f"Retrieved {len(data_records)} records from InfluxDB")

        result = {
            "query": query,
            "data": data_records,
            "count": len(data_records),
            "columns": list(df.columns),
        }

        return format_success_response(result)

    except McpError:
        raise
    except influxdb.exceptions.InfluxDBClientError as e:
        logger.error(f"InfluxDB client error: {e}", exc_info=True)
        return format_error_response("influxdb_client_error", str(e))
    except influxdb.exceptions.InfluxDBServerError as e:
        logger.error(f"InfluxDB server error: {e}", exc_info=True)
        return format_error_response("influxdb_server_error", str(e))
    except Exception as e:
        logger.error(f"Error querying InfluxDB: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


# ── InfluxDB helpers ──────────────────────────────────────────────────────────

def _make_influx_client(params: dict) -> influxdb.InfluxDBClient:
    return influxdb.InfluxDBClient(
        host=params["INFLUX_HOST"],
        port=params["INFLUX_PORT"],
        username=params["INFLUX_USERNAME"],
        password=params["INFLUX_PASSWORD"],
        database=params["INFLUX_DB_NAME"],
        ssl=False,
    )


def _find_device(connection, device_name: str):
    for d in dh_devices.list_devices(le_connection=connection):
        if d.name == device_name:
            return d
    return None


def _get_output_topic(tag) -> Optional[str]:
    for tp in (tag.topics or []):
        if tp.direction == "Output":
            return tp.topic
    return None


def _validate_time_range(time_range: str) -> None:
    if not re.fullmatch(r"\d+(ms|[usmhdw])", time_range):
        raise McpError(ErrorData(
            code=INVALID_PARAMS,
            message=f"Invalid time_range '{time_range}'. Use InfluxDB format e.g. '1h', '30m', '7d'.",
        ))


# ── New tools ─────────────────────────────────────────────────────────────────

async def list_influxdb_measurements(
    request: Request, arguments: dict
) -> list[TextContent]:
    params = get_influx_connection_params(request)
    try:
        client = _make_influx_client(params)
        rs = client.query("SHOW MEASUREMENTS")
        measurements = sorted(pt["name"] for pt in rs.get_points())
        return format_success_response({
            "database": params["INFLUX_DB_NAME"],
            "count": len(measurements),
            "measurements": measurements,
        })
    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error listing measurements: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_device_historical_data(
    request: Request, arguments: dict
) -> list[TextContent]:
    try:
        device_query = (arguments.get("device_query") or "").strip()
        tag_name_query = (arguments.get("tag_name_query") or "").strip()
        time_range = arguments.get("time_range", "1h")
        limit = min(int(arguments.get("limit", 1000)), 100000)

        if not device_query:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'device_query' is required"))
        _validate_time_range(time_range)

        params = get_influx_connection_params(request)
        client = _make_influx_client(params)

        rs = client.query("SHOW MEASUREMENTS")
        all_measurements = [pt["name"] for pt in rs.get_points()]

        matches = difflib.get_close_matches(device_query, all_measurements, n=20, cutoff=0.35)
        if not matches:
            matches = [m for m in all_measurements if device_query.lower() in m.lower()]

        if tag_name_query:
            matches = [m for m in matches if tag_name_query.lower() in m.lower()]

        if not matches:
            return format_success_response({
                "device_query": device_query,
                "matched_measurements": [],
                "results": [],
                "message": "No measurements matched the query. Use list_influxdb_measurements to see available names.",
            })

        results = []
        for measurement in matches[:5]:
            try:
                q = f'SELECT * FROM "{measurement}" WHERE time > now() - {time_range} LIMIT {limit}'
                r = client.query(q, chunked=True, chunk_size=10000)
                pts = list(r.get_points())
                results.append({"measurement": measurement, "count": len(pts), "data": pts})
            except Exception as ex:
                results.append({"measurement": measurement, "error": str(ex)})

        return format_success_response({
            "device_query": device_query,
            "matched_measurements": matches,
            "time_range": time_range,
            "results": results,
            "total_records": sum(r.get("count", 0) for r in results),
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error in get_device_historical_data: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def query_tag_data(
    request: Request, arguments: dict
) -> list[TextContent]:
    try:
        device_name = (arguments.get("device_name") or "").strip()
        tag_name = (arguments.get("tag_name") or "").strip()
        tag_id = (arguments.get("tag_id") or "").strip()
        time_range = arguments.get("time_range", "1h")
        limit = min(int(arguments.get("limit", 500)), 500)

        if not device_name:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'device_name' is required"))
        if not tag_name and not tag_id:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'tag_name' or 'tag_id' is required"))
        _validate_time_range(time_range)

        connection = get_litmus_connection(request)
        device = _find_device(connection, device_name)
        if not device:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Device '{device_name}' not found. Use get_devicehub_devices to list devices."))

        tag_list = dh_tags.list_registers_from_single_device(device, le_connection=connection)
        if tag_id:
            tag = next((t for t in tag_list if t.id == tag_id), None)
            identifier = f"ID '{tag_id}'"
        else:
            tag = next((t for t in tag_list if t.tag_name == tag_name), None)
            identifier = f"name '{tag_name}'"

        if not tag:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Tag with {identifier} not found on device '{device_name}'."))

        output_topic = _get_output_topic(tag)
        if not output_topic:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Tag '{tag.tag_name}' has no output topic — no data in InfluxDB."))

        params = get_influx_connection_params(request)
        client = _make_influx_client(params)
        q = f'SELECT * FROM "{output_topic}" WHERE time > now() - {time_range} ORDER BY time DESC LIMIT {limit}'
        r = client.query(q, chunked=True, chunk_size=5000)
        pts = list(r.get_points())

        return format_success_response({
            "device_name": device_name,
            "tag_name": tag.tag_name,
            "tag_id": tag.id,
            "measurement": output_topic,
            "time_range": time_range,
            "count": len(pts),
            "data": pts,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error in query_tag_data: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_tag_statistics(
    request: Request, arguments: dict
) -> list[TextContent]:
    try:
        device_name = (arguments.get("device_name") or "").strip()
        tag_name = (arguments.get("tag_name") or "").strip()
        tag_id = (arguments.get("tag_id") or "").strip()
        time_range = arguments.get("time_range", "1h")

        if not device_name:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'device_name' is required"))
        if not tag_name and not tag_id:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'tag_name' or 'tag_id' is required"))
        _validate_time_range(time_range)

        connection = get_litmus_connection(request)
        device = _find_device(connection, device_name)
        if not device:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Device '{device_name}' not found."))

        tag_list = dh_tags.list_registers_from_single_device(device, le_connection=connection)
        if tag_id:
            tag = next((t for t in tag_list if t.id == tag_id), None)
        else:
            tag = next((t for t in tag_list if t.tag_name == tag_name), None)

        if not tag:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Tag not found on device '{device_name}'."))

        output_topic = _get_output_topic(tag)
        if not output_topic:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Tag '{tag.tag_name}' has no output topic."))

        params = get_influx_connection_params(request)
        client = _make_influx_client(params)
        q = (
            f'SELECT mean("value") AS mean, min("value") AS min, max("value") AS max, '
            f'count("value") AS count, stddev("value") AS stddev '
            f'FROM "{output_topic}" WHERE time > now() - {time_range}'
        )
        r = client.query(q)
        pts = list(r.get_points())
        stats = pts[0] if pts else {}

        mean_v = stats.get("mean")
        std_v = stats.get("stddev")
        if mean_v is not None and std_v is not None:
            stats["baseline_low"] = mean_v - 2 * std_v
            stats["baseline_high"] = mean_v + 2 * std_v

        return format_success_response({
            "device_name": device_name,
            "tag_name": tag.tag_name,
            "tag_id": tag.id,
            "measurement": output_topic,
            "time_range": time_range,
            "statistics": stats,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error in get_tag_statistics: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))


async def get_device_data_for_inference(
    request: Request, arguments: dict
) -> list[TextContent]:
    try:
        device_name = (arguments.get("device_name") or "").strip()
        time_range = arguments.get("time_range", "1h")
        include_statistics = bool(arguments.get("include_statistics", True))
        sample_size = min(int(arguments.get("sample_size", 20)), 100)

        if not device_name:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="'device_name' is required"))
        _validate_time_range(time_range)

        connection = get_litmus_connection(request)
        device = _find_device(connection, device_name)
        if not device:
            raise McpError(ErrorData(code=INVALID_PARAMS,
                message=f"Device '{device_name}' not found."))

        params = get_influx_connection_params(request)
        client = _make_influx_client(params)

        tag_list = dh_tags.list_registers_from_single_device(device, le_connection=connection)

        tags_data = []
        for tag in tag_list:
            output_topic = _get_output_topic(tag)
            entry = {
                "tag_name": tag.tag_name,
                "tag_id": tag.id,
                "value_type": tag.value_type,
                "measurement": output_topic,
            }

            if output_topic and include_statistics:
                try:
                    q = (
                        f'SELECT mean("value") AS mean, min("value") AS min, max("value") AS max, '
                        f'count("value") AS count, stddev("value") AS stddev '
                        f'FROM "{output_topic}" WHERE time > now() - {time_range}'
                    )
                    r = client.query(q)
                    pts = list(r.get_points())
                    if pts:
                        s = pts[0]
                        mean_v, std_v = s.get("mean"), s.get("stddev")
                        if mean_v is not None and std_v is not None:
                            s["baseline_low"] = mean_v - 2 * std_v
                            s["baseline_high"] = mean_v + 2 * std_v
                        entry["statistics"] = s
                except Exception:
                    pass

            if output_topic and sample_size > 0:
                try:
                    q = f'SELECT * FROM "{output_topic}" WHERE time > now() - {time_range} ORDER BY time DESC LIMIT {sample_size}'
                    r = client.query(q)
                    entry["recent_samples"] = list(r.get_points())
                except Exception:
                    pass

            tags_data.append(entry)

        driver_name = None
        try:
            driver_name = getattr(device.driver, "name", None) or str(device.driver)
        except Exception:
            pass

        return format_success_response({
            "device": {
                "name": device.name,
                "id": device.id,
                "driver": driver_name,
                "description": device.description,
            },
            "time_range": time_range,
            "sample_size": sample_size,
            "tag_count": len(tags_data),
            "tags": tags_data,
        })

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error in get_device_data_for_inference: {e}", exc_info=True)
        return format_error_response("query_failed", str(e))
