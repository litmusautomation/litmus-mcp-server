import json
from mcp.types import TextContent


def format_success_response(data: dict) -> list[TextContent]:
    """Format a successful response."""
    result = {"success": True, **data}
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


def format_error_response(error_code: str, message: str, **kwargs) -> list[TextContent]:
    """Format an error response."""
    result = {"success": False, "error": error_code, "message": message, **kwargs}
    return [TextContent(type="text", text=json.dumps(result, indent=2))]
