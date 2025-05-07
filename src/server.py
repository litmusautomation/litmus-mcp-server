import os
import json
from typing import Literal, Any

import nats
import asyncio
from datetime import datetime
from numpy import zeros

from utils import ssl_config, NATS_SOURCE, NATS_PORT, MCP_PORT

from mcp.server.fastmcp import FastMCP
from litmussdk.devicehub import devices, tags
from litmussdk.devicehub.drivers import driver_templates, DriverRecord
from litmussdk.marketplace import list_all_containers, run_container
from litmussdk.system import network, device_management
from litmussdk.utils.env import update_env_variable


# Create an MCP server
mcp = FastMCP("LitmusMCPServer")
mcp.settings.port = MCP_PORT


@mcp.tool()
def get_litmusedge_driver_list() -> list[str]:
    """
    Get List of Litmus Edge Drivers supported by Litmus MCP Server

    Returns:
        List of string driver names
    """
    list_drivers = [v for v in dir(driver_templates) if not v.startswith("_")]

    return list_drivers


@mcp.tool()
def get_devicehub_devices() -> dict:
    """
    Retrieve all current DeviceHub devices configured on Litmus Edge.

    Returns:
        Dictionary of devices keyed by device name
    """
    output = {}
    list_devices = devices.list_devices()
    for current_device in list_devices:
        output[current_device.name] = current_device.__dict__

    return output


@mcp.tool()
def get_devicehub_device_tags(device_name: str) -> dict:
    """
    Retrieve all Tags of a single DeviceHub device configured on Litmus Edge.

    Args:
        device_name (str): Device name from where to grab tags

    Returns:
        Dictionary of tags keyed by tag name
    """
    output = {}
    requested_device = None
    list_devices = devices.list_devices()
    for current_device in list_devices:
        if device_name == current_device.name:
            requested_device = current_device
    if requested_device is None:
        raise Exception(f"Device with name {device_name} not found")

    list_tags = tags.list_registers_from_single_device(requested_device)
    for current_tag in list_tags:
        output[current_tag.tag_name] = current_tag.__dict__

    return output


@mcp.tool()
def get_current_value_of_devicehub_tag(
    device_name: str, tag_name: str | None, tag_id: str | None = None
) -> dict | str:
    """
    Retrieve current value of a single DeviceHub device tag configured on Litmus Edge.

    Args:
        device_name:
        tag_name: Tag name from where to grab tags
        tag_id: (optional) Tag ID from where to grab tags

    Returns:
        Current value from raw topic
    """
    if not tag_name and not tag_id:
        raise Exception("Either a Tag Name or Tag ID are required")

    requested_device = None
    list_devices = devices.list_devices()
    for current_device in list_devices:
        if device_name == current_device.name:
            requested_device = current_device
    if requested_device is None:
        raise Exception(f"Device with name {device_name} not found")

    list_tags = tags.list_registers_from_single_device(requested_device)
    if tag_name:
        requested_tag = next(
            (tag for tag in list_tags if tag.tag_name == tag_name), None
        )
    else:
        requested_tag = next((tag for tag in list_tags if tag.id == tag_id), None)

    if requested_tag is None:
        if tag_name:
            raise Exception(f"Tag with name '{tag_name}' not found")
        else:
            raise Exception(f"Tag with ID '{tag_id}' not found")

    requested_value_from_topic = next(
        (topic.topic for topic in requested_tag.topics if topic.direction == "Output"),
        "",
    )
    output = asyncio.run(get_current_value_on_topic(requested_value_from_topic))

    return output


@mcp.tool()
def create_devicehub_device(
    name: str,
    selected_driver: str,
):
    """
    Create a DeviceHub device on the connected Litmus Edge instance.

    The DeviceHub module supports various protocols and manufacturers,
    allowing register-based data polling via driver templates.

    Args:
        name: Name of the new device.
        selected_driver: Driver template to use. Use `list_supported_drivers_on_edge` to view available options.
    """
    listed_driver = vars(driver_templates).get(selected_driver)
    driver = DriverRecord.get(listed_driver.id)
    properties = driver.get_default_properties()

    device = devices.Device(name=name, properties=properties, driver=driver.id)
    created_device = devices.create_device(device)
    return created_device


@mcp.tool()
def update_environment_config(
    key: Literal[
        "EDGE_URL",
        "EDGE_API_CLIENT_ID",
        "EDGE_API_CLIENT_SECRET",
        "VALIDATE_CERTIFICATE",
    ],
    value: str,
) -> str:
    """
    Update Environment variables Config file for connecting to Litmus Edge

    Args:
        key (str): Keys such as EDGE_URL, EDGE_API_CLIENT_ID, EDGE_API_CLIENT_SECRET, VALIDATE_CERTIFICATE
        value (str): Config value
    """
    update_env_variable(key=key, value=value)

    return f"Config key {key} updated to {value}"


@mcp.tool()
def get_current_environment_config() -> dict:
    """
    Get the current environment configuration used for connecting to Litmus Edge.

    Returns:
        Dictionary of environment variable names and their values.
    """
    return {
        "EDGE_URL": os.environ.get("EDGE_URL", ""),
        "EDGE_API_CLIENT_ID": os.environ.get("EDGE_API_CLIENT_ID", ""),
        "VALIDATE_CERTIFICATE": os.environ.get("VALIDATE_CERTIFICATE", ""),
    }


@mcp.tool()
def get_litmusedge_friendly_name() -> str:
    """
    Get friendly name of LitmusEdge Device

    Returns:
         Device friendly name
    """
    return network.get_friendly_name()


@mcp.tool()
def set_litmusedge_friendly_name(new_friendly_name: str) -> None:
    """
    Change friendly name of LitmusEdge Device

    Args:
        new_friendly_name: New friendly name for the Device
    """
    return network.set_friendly_name(new_friendly_name)


@mcp.tool()
def get_cloud_activation_status() -> dict[str, Any]:
    """
    Get cloud activation status for the connection between Litmus Edge and Litmus Edge Manager

    Returns:
        Dictionary of cloud activation status
    """
    return device_management.show_cloud_registration_status()


@mcp.tool()
def get_all_containers_on_litmusedge() -> list[dict[str, Any]]:
    """
    List all containers in marketplace on Litmus Edge

    Returns:
        Array of all container/s details
    """
    return list_all_containers()


@mcp.tool()
def run_docker_container_on_litmusedge(docker_run_command: str) -> str:
    """
    Run a container in Litmus Edge marketplace with the docker command in the body.
    This command runs on the litmus edge marketplace, not on the host system of the MCP server

    Args:
        docker_run_command: Docker run command to be run in string

    Returns:
        ID of container created
    """
    return run_container(docker_run_command)["id"]


@mcp.tool()
async def get_current_value_on_topic(
    topic: str,
    nats_source: str | None = None,
    nats_port: str | None = None,
) -> dict:
    """
    Subscribe to current value on a topic on Litmus Edge.
    Change the global variable NATS_STATUS to "False" to end current subscription

    Args:
        topic: topic to subscribe to
        nats_source: nats source, defaults to 10.30.50.1
        nats_port: nats port, defaults to 4222

    Returns:
        Nats Subscription message
    """
    nats_source = nats_source or NATS_SOURCE
    nats_port = nats_port or NATS_PORT

    stop_event = asyncio.Event()

    final_message = await nc_single_topic(nats_source, nats_port, topic, stop_event)
    return final_message


@mcp.tool()
async def get_multiple_values_from_topic(
    topic: str,
    num_samples: int = 10,
    nats_source: str | None = None,
    nats_port: str | None = None,
) -> object:
    """
    Get multiple values from a topic, for plotting or just returning a dictionary of value arrays

    Args:
        topic: NATS topic to subscribe to
        num_samples: Number of messages to collect before plotting
        nats_source: NATS source IP, defaults to 10.30.50.1
        nats_port: NATS port, defaults to 4222

    Returns:
        Dictionary with timestamp and values as X and Y
    """
    nats_source = nats_source or NATS_SOURCE
    nats_port = nats_port or NATS_PORT

    stop_event = asyncio.Event()

    output = await collect_multiple_values_from_topic(
        nats_source, nats_port, topic, stop_event, num_samples
    )

    return output


async def nc_single_topic(
    nats_source: str,
    nats_port: str,
    nats_subscription_topic: str,
    stop_event: asyncio.Event,
) -> dict:
    """
    Subscribe to a single topic, and return a single message for the topic

    Args:
        nats_source: NATS source IP, defaults to 10.30.50.1
        nats_port: NATS port, defaults to 4222
        nats_subscription_topic: NATS topic to subscribe to
        stop_event: Asyncio Event to stop collecting values

    Returns:
        Single message from the subscribed topic
    """
    ssl_context = ssl_config()
    nc = await nats.connect(f"nats://{nats_source}:{nats_port}", tls=ssl_context)

    result_message = {}

    async def message_handler(msg):
        nonlocal result_message
        if result_message:
            stop_event.set()

        data = msg.data.decode()
        message = json.loads(data)
        result_message = message

    await nc.subscribe(nats_subscription_topic, cb=message_handler)
    await stop_event.wait()
    await nc.drain()

    return result_message


async def collect_multiple_values_from_topic(
    nats_source: str,
    nats_port: str,
    topic: str,
    stop_event: asyncio.Event,
    num_samples: int = 10,
) -> object:
    """
    Collect multiple values from a topic, for plotting or just returning a dictionary

    Args:
        nats_source: NATS source IP, defaults to 10.30.50.1
        nats_port: NATS port, defaults to 4222
        topic: NATS topic to subscribe to
        stop_event: Asyncio Event to stop collecting values
        num_samples: Number of messages to collect

    Returns:
        Dict of results
    """
    ssl_context = ssl_config()
    nc = await nats.connect(f"nats://{nats_source}:{nats_port}", tls=ssl_context)

    results = {
        # "timestamps": zeros(num_samples),
        "humanTimestamps": ["" for _ in range(num_samples)],
        "values": zeros(num_samples),
    }
    counter = 0

    async def message_handler(msg):
        nonlocal counter, results
        data = msg.data.decode()
        payload = json.loads(data)

        value = payload["value"]
        timestamp = payload["timestamp"]
        human_ts = str(datetime.fromtimestamp(timestamp / 1000))

        if counter < num_samples:
            # results["timestamps"][counter] = timestamp
            results["values"][counter] = value
            results["humanTimestamps"][counter] = human_ts
            counter += 1
        else:
            stop_event.set()
        #   # Sliding window
        #   if config["counter"] < num_samples:
        #       i = config["counter"]
        #       results["values"][i] = value
        #       results["timestamps"][i] = timestamp
        #       results["humanTimestamps"][i] = human_ts
        #       config["counter"] += 1
        #   else:
        #     config["counter"] = 0
        #     results["values"][:-1] = results["values"][1:]
        #     results["timestamps"][:-1] = results["timestamps"][1:]
        #     results["humanTimestamps"][:-1] = results["humanTimestamps"][1:]
        #
        #     results["values"][-1] = value
        #     results["timestamps"][-1] = timestamp
        #     results["humanTimestamps"][-1] = human_ts
        #
        # if config["counter"] >= num_samples:
        #     stop_event.set()

    await nc.subscribe(topic, cb=message_handler)
    await stop_event.wait()
    await nc.drain()

    return results


if __name__ == "__main__":
    mcp.run(transport="stdio")
