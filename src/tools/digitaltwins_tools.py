from starlette.requests import Request
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS
from mcp.types import TextContent

from litmussdk.digital_twins import (
    list_models,
    list_all_instances,
    create_instance,
    list_static_attributes,
    list_dynamic_attributes,
    list_transformations,
    get_hierarchy,
    save_hierarchy,
)
from utils.auth import get_litmus_connection
from utils.formatting import format_success_response, format_error_response
from config import logger


async def list_digital_twin_models_tool(request: Request) -> list[TextContent]:
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


async def list_static_attributes_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Lists static attributes for a model or instance.

    Must provide either model_id OR instance_id (not both).
    """
    try:
        connection = get_litmus_connection(request)

        model_id = arguments.get("model_id")
        instance_id = arguments.get("instance_id")

        # Validate that exactly one is provided
        if not model_id and not instance_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="Either 'model_id' or 'instance_id' parameter is required",
                )
            )

        if model_id and instance_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="Cannot specify both 'model_id' and 'instance_id'",
                )
            )

        # List static attributes
        attributes = list_static_attributes(
            model_id=model_id, instance_id=instance_id, le_connection=connection
        )

        logger.info(
            f"Retrieved {len(attributes)} static attributes for "
            f"{'model ' + model_id if model_id else 'instance ' + instance_id}"
        )

        result = {
            "static_attributes": attributes,
            "count": len(attributes),
        }

        if model_id:
            result["model_id"] = model_id
        if instance_id:
            result["instance_id"] = instance_id

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error listing static attributes: {e}", exc_info=True)
        return format_error_response("list_static_attributes_failed", str(e))


async def list_dynamic_attributes_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Lists dynamic attributes for a model or instance.

    Must provide either model_id OR instance_id (not both).
    """
    try:
        connection = get_litmus_connection(request)

        model_id = arguments.get("model_id")
        instance_id = arguments.get("instance_id")

        # Validate that exactly one is provided
        if not model_id and not instance_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="Either 'model_id' or 'instance_id' parameter is required",
                )
            )

        if model_id and instance_id:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="Cannot specify both 'model_id' and 'instance_id'",
                )
            )

        # List dynamic attributes
        attributes = list_dynamic_attributes(
            model_id=model_id, instance_id=instance_id, le_connection=connection
        )

        logger.info(
            f"Retrieved {len(attributes)} dynamic attributes for "
            f"{'model ' + model_id if model_id else 'instance ' + instance_id}"
        )

        result = {
            "dynamic_attributes": attributes,
            "count": len(attributes),
        }

        if model_id:
            result["model_id"] = model_id
        if instance_id:
            result["instance_id"] = instance_id

        return format_success_response(result)

    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error listing dynamic attributes: {e}", exc_info=True)
        return format_error_response("list_dynamic_attributes_failed", str(e))


async def list_transformations_tool(
    request: Request, arguments: dict
) -> list[TextContent]:
    """
    Lists transformations for a digital twin model.

    Transformations define how data is processed within the model.
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

        # List transformations
        transformations = list_transformations(
            model_id=model_id, le_connection=connection
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
