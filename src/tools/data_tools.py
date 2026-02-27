import asyncio
import nats
import json
import pandas as pd
from typing import Optional
from datetime import datetime

from config import logger, ssl_config
from config import NATS_PORT, NATS_SOURCE

from utils.formatting import format_success_response, format_error_response
from utils.auth import get_nats_connection_params, get_influx_connection_params
from utils.async_utils import run_sync

from numpy import zeros
from starlette.requests import Request
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS
from mcp.types import TextContent

import influxdb

INFLUXDB_AVAILABLE = True
# except ImportError:
#     INFLUXDB_AVAILABLE = False
#     logger.warning(
#         "influxdb library not available. Historical data queries will not work."
#     )


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
    if request:
        try:
            params = get_nats_connection_params(request)
            nats_source = params["nats_source"]
            nats_port = params["nats_port"]
            nats_user = params.get("nats_user")
            nats_password = params.get("nats_password")
        except McpError:
            # Fall back to provided parameters or config defaults
            nats_source = nats_source or NATS_SOURCE
            nats_port = nats_port or NATS_PORT
            nats_user = None
            nats_password = None
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
        try:
            params = get_nats_connection_params(request)
            nats_source = params["nats_source"]
            nats_port = params["nats_port"]
            nats_user = params.get("nats_user")
            nats_password = params.get("nats_password")
        except McpError:
            # Fall back to provided parameters or config defaults
            nats_source = nats_source or NATS_SOURCE
            nats_port = nats_port or NATS_PORT
            nats_user = None
            nats_password = None

        stop_event = asyncio.Event()

        output = await _collect_multiple_values_from_topic(
            nats_source,
            nats_port,
            topic,
            stop_event,
            num_samples,
            nats_user=nats_user,
            nats_password=nats_password,
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


def _get_connect_options(nats_source, nats_port, nats_user, nats_password):

    connect_options = {"servers": [f"nats://{nats_source}:{nats_port}"]}

    ssl_context = ssl_config()
    connect_options["tls"] = ssl_context

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
) -> dict:
    """
    Subscribe to a single topic and return a single message.
    """

    connect_options = _get_connect_options(
        nats_source, nats_port, nats_user, nats_password
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

    await nc.subscribe(nats_subscription_topic, cb=message_handler)
    await stop_event.wait()
    await nc.drain()

    return result_message


async def _collect_multiple_values_from_topic(
    nats_source: str,
    nats_port: str,
    topic: str,
    stop_event: asyncio.Event,
    num_samples: int = 10,
    nats_user: Optional[str] = None,
    nats_password: Optional[str] = None,
) -> dict:
    """
    Collect multiple values from a topic for plotting or analysis.
    """
    connect_options = _get_connect_options(
        nats_source, nats_port, nats_user, nats_password
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

    await nc.subscribe(topic, cb=message_handler)
    await stop_event.wait()
    await nc.drain()

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
        result = await run_sync(influx_client.query, query, chunked=True, chunk_size=10000)
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
