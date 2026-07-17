import asyncio

from starlette.requests import Request
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS
from mcp.types import TextContent, ToolAnnotations

from litmussdk.digital_twins import (
    list_models,
    create_model,
    list_all_instances,
    create_instance,
    get_hierarchy,
    save_hierarchy,
)
from utils.auth import get_litmus_connection
from utils.formatting import format_success_response, format_error_response
from config import logger
from .sdk_cli_tools import run_cli_function, CLIFunctionError


async def list_digital_twin_models_tool(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """
    Retrieves all digital twin models configured on Litmus Edge.

    Returns information about each model including ID, name, description, and version.
    """
    try:
        connection = get_litmus_connection(request)
        models = list_models(le_connection=connection)

        logger.info(f"Retrieved {len(models)} digital twin models")

        result = {
            "models": models,
            "count": len(models),
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error listing digital twin models: {e}", exc_info=True)
        return format_error_response("list_models_failed", str(e))


async def create_digital_twin_model_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Creates a new digital twin model on Litmus Edge.

    Requires model_name. Optional model_description and model_type ('ASSET' only).
    """
    try:
        connection = get_litmus_connection(request)

        model_name = arguments.get("model_name")
        model_description = arguments.get("model_description", "")
        model_type = arguments.get("model_type", "ASSET")

        if not model_name:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'model_name' parameter is required"
                )
            )

        if model_type != "ASSET":
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="'model_type' must be 'ASSET' (only supported type)",
                )
            )

        model = create_model(
            model_name=model_name,
            model_description=model_description,
            model_type=model_type,
            le_connection=connection,
        )

        logger.info(f"Created digital twin model '{model_name}'")

        result = {
            "model": model,
            "message": f"Model '{model_name}' created successfully",
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error creating digital twin model: {e}", exc_info=True)
        return format_error_response("create_model_failed", str(e))


async def list_digital_twin_instances_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Retrieves all digital twin instances or instances for a specific model.

    Can optionally filter by model_id to get only instances of a specific model.
    """
    try:
        connection = get_litmus_connection(request)
        model_id = arguments.get("model_id")

        if model_id:
            # Import here to avoid circular dependency
            from litmussdk.digital_twins import get_instance_by_model

            instances = get_instance_by_model(
                model_id=model_id, le_connection=connection
            )
            logger.info(f"Retrieved {len(instances)} instances for model {model_id}")
        else:
            instances = list_all_instances(le_connection=connection)
            logger.info(f"Retrieved {len(instances)} digital twin instances")

        result = {
            "instances": instances,
            "count": len(instances),
        }

        if model_id:
            result["model_id"] = model_id

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error listing digital twin instances: {e}", exc_info=True)
        return format_error_response("list_instances_failed", str(e))


async def create_digital_twin_instance_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Creates a new digital twin instance from a model.

    Requires model_id, instance name, and topic for data access.
    """
    try:
        connection = get_litmus_connection(request)

        # Extract required parameters
        model_id = arguments.get("model_id")
        instance_name = arguments.get("instance_name")
        instance_topic = arguments.get("instance_topic")

        # Extract optional parameters
        instance_interval = arguments.get("instance_interval", 1)
        instance_flat_hierarchy = arguments.get("instance_flat_hierarchy", False)

        # Validate required parameters
        if not model_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'model_id' parameter is required"
                )
            )

        if not instance_name:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'instance_name' parameter is required"
                )
            )

        if not instance_topic:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="'instance_topic' parameter is required",
                )
            )

        # Create the instance
        instance = create_instance(
            model_id=model_id,
            instance_name=instance_name,
            instance_topic=instance_topic,
            instance_interval=instance_interval,
            instance_flat_hierarchy=instance_flat_hierarchy,
            le_connection=connection,
        )

        logger.info(
            f"Created digital twin instance '{instance_name}' from model {model_id}"
        )

        result = {
            "instance": instance,
            "message": f"Instance '{instance_name}' created successfully",
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error creating digital twin instance: {e}", exc_info=True)
        return format_error_response("create_instance_failed", str(e))


# Attribute tools are backed by the litmus-cli Go binary rather than the
# Python SDK: the CLI shares its connection layer with the SDK fallback
# tools (including LEM bridge routing) and returns plain JSON without
# pydantic validation.

_ATTRIBUTE_FUNCTIONS = {
    "static": ("le.digitaltwins.ListStaticAttributes", "static_attributes"),
    "dynamic": ("le.digitaltwins.ListDynamicAttributes", "dynamic_attributes"),
}

_INSTANCE_FANOUT_CONCURRENCY = 4


async def _cli_list_instances(request: Request) -> list[dict]:
    instances = (
        await run_cli_function(request, "le.digitaltwins.ListAllInstances", {}) or []
    )
    return [i for i in instances if isinstance(i, dict)]


async def _list_attributes_impl(
    request: Request, arguments: dict, kind: str
) -> list[TextContent]:
    function, result_key = _ATTRIBUTE_FUNCTIONS[kind]
    model_id = (arguments.get("model_id") or "").strip()
    instance_id = (arguments.get("instance_id") or "").strip()
    instance_name = (arguments.get("instance_name") or "").strip()
    all_instances = bool(arguments.get("all_instances"))

    if sum(map(bool, (model_id, instance_id, instance_name, all_instances))) != 1:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    "Provide exactly one of 'model_id', 'instance_id', "
                    "'instance_name', or 'all_instances'."
                ),
            )
        )

    try:
        if all_instances:
            instances = await _cli_list_instances(request)
            semaphore = asyncio.Semaphore(_INSTANCE_FANOUT_CONCURRENCY)

            async def _one(instance: dict) -> dict:
                iid = instance.get("ID")
                entry = {
                    "instance_id": iid,
                    "instance_name": instance.get("Name"),
                }
                async with semaphore:
                    try:
                        attributes = (
                            await run_cli_function(
                                request, function, {"instanceID": iid}
                            )
                            or []
                        )
                        entry[result_key] = attributes
                        entry["count"] = len(attributes)
                    except CLIFunctionError as e:
                        entry["error"] = str(e)
                return entry

            per_instance = list(
                await asyncio.gather(*[_one(instance) for instance in instances])
            )
            total = sum(e.get("count", 0) for e in per_instance)
            logger.info(
                f"Retrieved {kind} attributes for {len(per_instance)} digital "
                f"twin instances ({total} attributes)"
            )
            return format_success_response(
                {
                    "instances": per_instance,
                    "instance_count": len(per_instance),
                    "count": total,
                }
            )

        if instance_name:
            instances = await _cli_list_instances(request)
            match = next(
                (
                    i
                    for i in instances
                    if (i.get("Name") or "").lower() == instance_name.lower()
                ),
                None,
            )
            if match is None:
                available = sorted(
                    str(i.get("Name")) for i in instances if i.get("Name")
                )
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=(
                            f"Digital twin instance '{instance_name}' not found. "
                            f"Available instances: {available}"
                        ),
                    )
                )
            instance_id = match.get("ID") or ""

        cli_args = (
            {"instanceID": instance_id} if instance_id else {"modelID": model_id}
        )
        attributes = await run_cli_function(request, function, cli_args) or []

        logger.info(
            f"Retrieved {len(attributes)} {kind} attributes for "
            f"{'instance ' + instance_id if instance_id else 'model ' + model_id}"
        )

        result = {
            result_key: attributes,
            "count": len(attributes),
        }
        if model_id:
            result["model_id"] = model_id
        if instance_id:
            result["instance_id"] = instance_id
        if instance_name:
            result["instance_name"] = instance_name

        return format_success_response(result)

    except McpError:
        raise
    except CLIFunctionError as e:
        return format_error_response(f"list_{kind}_attributes_failed", str(e))
    except Exception as e:
        logger.error(f"Error listing {kind} attributes: {e}", exc_info=True)
        return format_error_response(f"list_{kind}_attributes_failed", str(e))


async def list_static_attributes_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Lists static attributes for a model, an instance, or every instance.

    Provide exactly one of model_id, instance_id, instance_name, all_instances.
    """
    return await _list_attributes_impl(request, arguments, "static")


async def list_dynamic_attributes_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Lists dynamic attributes for a model, an instance, or every instance.

    Provide exactly one of model_id, instance_id, instance_name, all_instances.
    """
    return await _list_attributes_impl(request, arguments, "dynamic")


async def list_transformations_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Lists transformations for a digital twin model.

    Transformations define how data is processed within the model.
    """
    try:
        model_id = (arguments.get("model_id") or "").strip()
        if not model_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'model_id' parameter is required"
                )
            )

        transformations = (
            await run_cli_function(
                request, "le.digitaltwins.ListTransformations", {"modelID": model_id}
            )
            or []
        )

        logger.info(
            f"Retrieved {len(transformations)} transformations for model {model_id}"
        )

        result = {
            "transformations": transformations,
            "count": len(transformations),
            "model_id": model_id,
        }

        return format_success_response(result)

    except McpError:
        raise
    except CLIFunctionError as e:
        return format_error_response("list_transformations_failed", str(e))
    except Exception as e:
        logger.error(f"Error listing transformations: {e}", exc_info=True)
        return format_error_response("list_transformations_failed", str(e))


async def get_hierarchy_tool(request: Request, arguments: dict) -> list[TextContent]:
    """
    Gets the hierarchy configuration for a digital twin model.

    The hierarchy defines the structural relationships within the model.
    """
    try:
        connection = get_litmus_connection(request)

        model_id = arguments.get("model_id")

        # Validate required parameter
        if not model_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'model_id' parameter is required"
                )
            )

        # Get hierarchy
        hierarchy = get_hierarchy(model_id=model_id, le_connection=connection)

        logger.info(f"Retrieved hierarchy for model {model_id}")

        result = {
            "hierarchy": hierarchy,
            "model_id": model_id,
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error getting hierarchy: {e}", exc_info=True)
        return format_error_response("get_hierarchy_failed", str(e))


async def save_hierarchy_tool(request: Request, arguments: dict) -> list[TextContent]:
    """
    Saves a new hierarchy configuration to a digital twin model.

    The hierarchy must be in the exact JSON format used by Digital Twins.
    """
    try:
        connection = get_litmus_connection(request)

        model_id = arguments.get("model_id")
        hierarchy_json = arguments.get("hierarchy_json")

        # Validate required parameters
        if not model_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS, message="'model_id' parameter is required"
                )
            )

        if not hierarchy_json:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="'hierarchy_json' parameter is required",
                )
            )

        # Save hierarchy
        result_data = save_hierarchy(
            model_id=model_id,
            hierarchy_json=hierarchy_json,
            le_connection=connection,
        )

        logger.info(f"Saved hierarchy for model {model_id}")

        result = {
            "result": result_data,
            "model_id": model_id,
            "message": "Hierarchy saved successfully",
        }

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error saving hierarchy: {e}", exc_info=True)
        return format_error_response("save_hierarchy_failed", str(e))


TOOLS = [
    {
        "name": "list_digital_twin_models",
        "category": "digitaltwins.models",
        "annotations": ToolAnnotations(title="List Digital Twin Models", readOnlyHint=True),
        "description": (
            "Lists all Digital Twin models configured on Litmus Edge. "
            "Returns model information including ID, name, description, and version. "
            "Use this to see available models before creating instances or managing attributes."
        ),
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": list_digital_twin_models_tool,
    },
    {
        "name": "create_digital_twin_model",
        "category": "digitaltwins.models",
        "annotations": ToolAnnotations(title="Create Digital Twin Model", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Creates a new Digital Twin model on Litmus Edge. "
            "A model is the schema/template; create instances from it with create_digital_twin_instance. "
            "Only model_type 'ASSET' is supported."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "model_name": {
                    "type": "string",
                    "description": "Name of the model to create",
                },
                "model_description": {
                    "type": "string",
                    "description": "Optional description for the model",
                },
                "model_type": {
                    "type": "string",
                    "description": "Model type (only 'ASSET' is currently supported)",
                    "enum": ["ASSET"],
                    "default": "ASSET",
                },
            },
            "required": ["model_name"],
        },
        "handler": create_digital_twin_model_tool,
    },
    {
        "name": "list_digital_twin_instances",
        "category": "digitaltwins.instances",
        "annotations": ToolAnnotations(title="List Digital Twin Instances", readOnlyHint=True),
        "description": (
            "Lists all Digital Twin instances or instances for a specific model. "
            "Instances are runtime representations of models with actual data. "
            "Can optionally filter by model_id to get only instances of a specific model."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "Optional: Filter instances by model ID. If not provided, returns all instances.",
                },
            },
            "required": [],
        },
        "handler": list_digital_twin_instances_tool,
    },
    {
        "name": "create_digital_twin_instance",
        "category": "digitaltwins.instances",
        "annotations": ToolAnnotations(title="Create Digital Twin Instance", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Creates a new Digital Twin instance from an existing model. "
            "An instance is a runtime representation of a model that processes and publishes data. "
            "PREREQUISITE: a Digital Twin model MUST already exist - instances cannot be created "
            "without one. Call list_digital_twin_models first to find an existing model_id, or "
            "create_digital_twin_model to create one if none suits. "
            "Requires model_id (from an existing model), instance name, and NATS topic for data publication."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "ID of an EXISTING model to instantiate. Get this from list_digital_twin_models, or create a model first with create_digital_twin_model. Instance creation will fail if the model does not exist.",
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
        "handler": create_digital_twin_instance_tool,
    },
    {
        "name": "list_static_attributes",
        "category": "digitaltwins.attributes",
        "annotations": ToolAnnotations(title="List Static Attributes", readOnlyHint=True),
        "description": (
            "Lists static attributes for Digital Twin models or instances. "
            "Static attributes are fixed key-value pairs (e.g., serial number, location). "
            "Provide exactly ONE of: model_id, instance_id, instance_name, or "
            "all_instances=true. When the user asks about several or all twins, "
            "use all_instances=true (one call returns attributes per instance) "
            "instead of querying only the first instance."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "Model ID to get static attributes from",
                },
                "instance_id": {
                    "type": "string",
                    "description": "Instance ID to get static attributes from",
                },
                "instance_name": {
                    "type": "string",
                    "description": "Instance name (resolved to its ID, case-insensitive)",
                },
                "all_instances": {
                    "type": "boolean",
                    "description": "true to return static attributes for EVERY instance, grouped per instance",
                },
            },
            "required": [],
        },
        "handler": list_static_attributes_tool,
    },
    {
        "name": "list_dynamic_attributes",
        "category": "digitaltwins.attributes",
        "annotations": ToolAnnotations(title="List Dynamic Attributes", readOnlyHint=True),
        "description": (
            "Lists dynamic attributes for Digital Twin models or instances. "
            "Dynamic attributes are real-time data points (e.g., temperature, pressure, speed). "
            "Provide exactly ONE of: model_id, instance_id, instance_name, or "
            "all_instances=true. When the user asks about several or all twins, "
            "use all_instances=true (one call returns attributes per instance) "
            "instead of querying only the first instance."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "Model ID to get dynamic attributes from",
                },
                "instance_id": {
                    "type": "string",
                    "description": "Instance ID to get dynamic attributes from",
                },
                "instance_name": {
                    "type": "string",
                    "description": "Instance name (resolved to its ID, case-insensitive)",
                },
                "all_instances": {
                    "type": "boolean",
                    "description": "true to return dynamic attributes for EVERY instance, grouped per instance",
                },
            },
            "required": [],
        },
        "handler": list_dynamic_attributes_tool,
    },
    {
        "name": "list_transformations",
        "category": "digitaltwins.attributes",
        "annotations": ToolAnnotations(title="List Transformations", readOnlyHint=True),
        "description": (
            "Lists transformations configured for a Digital Twin model. "
            "Transformations define data processing rules and calculations within the model. "
            "Returns transformation schemas showing how data is transformed."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "Model ID to get transformations from",
                },
            },
            "required": ["model_id"],
        },
        "handler": list_transformations_tool,
    },
    {
        "name": "get_digital_twin_hierarchy",
        "category": "digitaltwins.hierarchy",
        "annotations": ToolAnnotations(title="Get Digital Twin Hierarchy", readOnlyHint=True),
        "description": (
            "Gets the hierarchy configuration for a Digital Twin model. "
            "The hierarchy defines the structural relationships and organization within the model. "
            "Returns the complete hierarchy structure in JSON format."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "Model ID to get hierarchy from",
                },
            },
            "required": ["model_id"],
        },
        "handler": get_hierarchy_tool,
    },
    {
        "name": "save_digital_twin_hierarchy",
        "category": "digitaltwins.hierarchy",
        "annotations": ToolAnnotations(title="Save Digital Twin Hierarchy", readOnlyHint=False, destructiveHint=True),
        "description": (
            "Saves a new hierarchy configuration to a Digital Twin model. "
            "The hierarchy must be in the exact JSON format used by Digital Twins. "
            "Use get_digital_twin_hierarchy first to see the expected format."
        ),
        "schema": {
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
        "handler": save_hierarchy_tool,
    },
]
