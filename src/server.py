import base64
import contextlib
import logging
import asyncio
import os
import warnings

from contextvars import ContextVar
from pathlib import Path as _Path

import urllib3
from mcp.server import Server

from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import Icon, Tool, TextContent
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR, METHOD_NOT_FOUND
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from config import MCP_PORT
from tools.devicehub_tools import TOOLS as _DH_TOOLS
from tools.dm_tools import TOOLS as _DM_TOOLS
from tools.marketplace_tools import TOOLS as _MKT_TOOLS
from tools.data_tools import TOOLS as _DATA_TOOLS
from tools.digitaltwins_tools import TOOLS as _DT_TOOLS
from tools.system_tools import TOOLS as _SYS_TOOLS
from tools.lem_tools import TOOLS as _LEM_TOOLS
from tools.sdk_cli_tools import TOOLS as _SDK_CLI_TOOLS
from tools.resource_tools import (
    get_documentation_resource_list,
    read_documentation_resource,
)

ALL_TOOLS = (
    _DH_TOOLS
    + _DM_TOOLS
    + _MKT_TOOLS
    + _DATA_TOOLS
    + _DT_TOOLS
    + _SYS_TOOLS
    + _LEM_TOOLS
    + _SDK_CLI_TOOLS
)
TOOL_BY_NAME: dict = {}
for _tool in ALL_TOOLS:
    if _tool["name"] in TOOL_BY_NAME:
        raise RuntimeError(f"Duplicate tool name: {_tool['name']}")
    TOOL_BY_NAME[_tool["name"]] = _tool

# Set up logging
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)

def _server_icons() -> list[Icon] | None:
    """Brand icon advertised in the initialize response, embedded as a data
    URI so it renders without network access (e.g. stdio clients)."""
    icon_path = _Path(__file__).resolve().parent.parent / "static" / "icon.png"
    try:
        encoded = base64.b64encode(icon_path.read_bytes()).decode()
    except OSError:
        return None
    return [
        Icon(
            src=f"data:image/png;base64,{encoded}",
            mimeType="image/png",
            sizes=["512x512"],
        )
    ]


def _server_version() -> str | None:
    """Project version from pyproject.toml; the project is a uv virtual
    project (not an installed distribution), so package metadata is absent."""
    pyproject = _Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        import tomllib

        with pyproject.open("rb") as f:
            return tomllib.load(f)["project"]["version"]
    except Exception:
        return None


# Create MCP server
mcp = Server(
    "LitmusMCPServer",
    version=_server_version(),
    website_url="https://litmus.io",
    icons=_server_icons(),
)

# Context variable to store request across async calls
current_request: ContextVar[Request | None] = ContextVar(
    "current_request", default=None
)


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
    return [
        ReadResourceContents(content=t, mime_type="text/plain") for t in text_contents
    ]


@mcp.list_tools()
async def handle_list_tools() -> list[Tool]:
    """Return all registered tools from the per-file TOOLS registries."""
    return [
        Tool(
            name=tool["name"],
            description=tool["description"],
            inputSchema=tool["schema"],
            annotations=tool.get("annotations"),
        )
        for tool in ALL_TOOLS
    ]


def _resolve_request():
    """Resolve the request carrying connection headers for the current tool call.

    Streamable HTTP attaches the Starlette request to each message
    (mcp.request_context.request); its handlers run in the session manager's
    task group, where the current_request context var is not set. SSE and
    stdio set current_request on the connection task instead.
    """
    try:
        ctx_request = mcp.request_context.request
    except LookupError:
        ctx_request = None
    if ctx_request is not None:
        return ctx_request
    return current_request.get()


@mcp.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """Dispatch a tool call by looking up the handler in TOOL_BY_NAME."""
    tool = TOOL_BY_NAME.get(name)
    if tool is None:
        logger.error(f"Unknown tool requested: {name}")
        raise McpError(
            ErrorData(code=METHOD_NOT_FOUND, message=f"Unknown tool: {name}")
        )

    request = _resolve_request()
    if request is None:
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message="Request context not available")
        )

    try:
        return await tool["handler"](request, arguments or {})
    except McpError:
        raise
    except Exception as e:
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
    logger.info(
        "Configuration from environment variables: EDGE_URL, EDGE_API_CLIENT_ID, EDGE_API_CLIENT_SECRET, NATS_*, INFLUX_*"
    )

    # Run with stdio transport
    async with stdio_server() as (read_stream, write_stream):
        await mcp.run(read_stream, write_stream, mcp.create_initialization_options())


# Streamable HTTP endpoint (spec 2025-03-26+). Stateless: every call carries
# its own connection headers, so there is no per-session state to keep.
session_manager = StreamableHTTPSessionManager(
    app=mcp,
    event_store=None,
    json_response=False,
    stateless=True,
)


class StreamableHTTPEndpoint:
    """Raw ASGI endpoint for /mcp (a class instance so Starlette's Route does
    not wrap it in request_response, which would double-send the response)."""

    async def __call__(self, scope, receive, send):
        await session_manager.handle_request(scope, receive, send)


handle_streamable_http = StreamableHTTPEndpoint()


@contextlib.asynccontextmanager
async def lifespan(app):
    """Run the streamable HTTP session manager for the app's lifetime."""
    async with session_manager.run():
        yield


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
        self.headers = HeaderDict(
            {
                "EDGE_API_CLIENT_ID": os.getenv("EDGE_API_CLIENT_ID", ""),
                "EDGE_API_CLIENT_SECRET": os.getenv("EDGE_API_CLIENT_SECRET", ""),
                "EDGE_URL": os.getenv("EDGE_URL", ""),
                "VALIDATE_CERTIFICATE": os.getenv("VALIDATE_CERTIFICATE", "false"),
                "EDGE_MANAGER_URL": os.getenv("EDGE_MANAGER_URL", ""),
                "EDGE_API_TOKEN": os.getenv("EDGE_API_TOKEN", ""),
                "EDGE_MANAGER_PROJECT_ID": os.getenv("EDGE_MANAGER_PROJECT_ID", ""),
                "EDGE_MANAGER_DEVICE_ID": os.getenv("EDGE_MANAGER_DEVICE_ID", ""),
                "EDGE_MANAGER_ADMIN_URL": os.getenv("EDGE_MANAGER_ADMIN_URL", ""),
                "NATS_SOURCE": os.getenv("NATS_SOURCE", ""),
                "NATS_PORT": os.getenv("NATS_PORT", ""),
                "NATS_USER": os.getenv("NATS_USER", ""),
                "NATS_PASSWORD": os.getenv("NATS_PASSWORD", ""),
                "NATS_TOKEN": os.getenv("NATS_TOKEN", ""),
                "NATS_TLS": os.getenv("NATS_TLS", "true"),
                "INFLUX_HOST": os.getenv("INFLUX_HOST", ""),
                "INFLUX_PORT": os.getenv("INFLUX_PORT", ""),
                "INFLUX_DB_NAME": os.getenv("INFLUX_DB_NAME", ""),
                "INFLUX_USERNAME": os.getenv("INFLUX_USERNAME", ""),
                "INFLUX_PASSWORD": os.getenv("INFLUX_PASSWORD", ""),
            }
        )
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
            "Please use the Streamable HTTP (/mcp) or SSE (/sse) transport with "
            "header-based authentication "
            "(EDGE_API_CLIENT_ID and EDGE_API_CLIENT_SECRET).",
        },
    )


async def health_check(request: Request):
    """Basic health check endpoint."""
    return JSONResponse({"status": "ok", "service": "litmus-mcp-server"})


# Wrap the SSE POST handler with our context-capturing middleware
wrapped_post_handler = ContextCapturingMiddleware(sse.handle_post_message)

# Create Starlette app with the Streamable HTTP, SSE and POST message routes
app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages", app=wrapped_post_handler),
        # OAuth discovery endpoints - return proper JSON errors
        Route(
            "/.well-known/oauth-authorization-server",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-authorization-server/sse",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-authorization-server/mcp",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route(
            "/.well-known/openid-configuration",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route(
            "/.well-known/openid-configuration/sse",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route(
            "/.well-known/openid-configuration/mcp",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route(
            "/sse/.well-known/openid-configuration",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource/sse",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        Route("/register", endpoint=oauth_not_supported, methods=["GET", "POST"]),
        # Health check endpoint
        Route("/health", endpoint=health_check, methods=["GET"]),
        Route(
            "/mcp/.well-known/openid-configuration",
            endpoint=oauth_not_supported,
            methods=["GET"],
        ),
        # Streamable HTTP transport; exact path (no Mount) so clients hitting
        # /mcp are served directly instead of being 307-redirected to /mcp/
        Route(
            "/mcp",
            endpoint=handle_streamable_http,
            methods=["POST", "GET", "DELETE"],
        ),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    import uvicorn
    from config import ENABLE_STDIO

    if ENABLE_STDIO:
        # STDIO mode - runs on stdin/stdout
        logger.info("STDIO mode enabled")
        asyncio.run(run_stdio_server())
    else:
        # HTTP mode - serves both Streamable HTTP and legacy SSE transports
        logger.info(f"HTTP mode enabled - Starting on port {MCP_PORT}")
        logger.info(f"Streamable HTTP endpoint: http://0.0.0.0:{MCP_PORT}/mcp")
        logger.info(f"SSE endpoint (legacy): http://0.0.0.0:{MCP_PORT}/sse")
        uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
