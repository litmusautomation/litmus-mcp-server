"""
Generic SDK fallback tools backed by the standalone `litmus-cli` Go binary.

The curated tools in the other modules cover the common workflows. These two
expose the full generated SDK surface (~550 functions) for everything else:

  - litmus_sdk_discover  ->  `litmus-cli list [prefix]`
  - litmus_sdk_call      ->  `litmus-cli run <dotted.path> --args '{...}'`

Connection credentials are forwarded from the request headers to the
subprocess environment. The header names used by this server and the CLI's
environment variables are intentionally identical, so forwarding is verbatim.
`LITMUS_CONFIG_DIR` is pointed at an isolated per-process directory so a
profile saved on the host can never leak into a request, and no request can
write one.

`litmus_sdk_call` is approval-gated: it fails unless `user_approved` is true,
which the model may only set after the user explicitly approved the exact
function and arguments in conversation.
"""

import asyncio
import json
import os
import shutil
import tempfile
from typing import Optional

from starlette.requests import Request
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR
from mcp.types import TextContent, ToolAnnotations

from config import logger
from utils.formatting import format_success_response, format_error_response

# Request headers forwarded verbatim to the CLI environment. The CLI resolves
# config as profile < .env < environment < flags, so these always win over
# anything on disk.
_FORWARDED_HEADERS = (
    "EDGE_URL",
    "EDGE_API_CLIENT_ID",
    "EDGE_API_CLIENT_SECRET",
    "USE_LEM_BRIDGE",
    "EDGE_MANAGER_URL",
    "EDGE_API_TOKEN",
    "EDGE_MANAGER_PROJECT_ID",
    "EDGE_MANAGER_DEVICE_ID",
    "VALIDATE_CERTIFICATE",
    "TIMEOUT_SECONDS",
)

_LEM_BRIDGE_HEADERS = (
    "EDGE_MANAGER_URL",
    "EDGE_API_TOKEN",
    "EDGE_MANAGER_PROJECT_ID",
    "EDGE_MANAGER_DEVICE_ID",
)

_CLI_TIMEOUT_SECONDS = 120
_RELEASES_URL = "https://github.com/litmusautomation/litmus-sdk-releases/releases"

_isolated_dir: Optional[str] = None


def _get_isolated_dir() -> str:
    """Per-process scratch directory for CLI config and caches, so requests
    never read or write a profile in the host's ~/.litmus."""
    global _isolated_dir
    if _isolated_dir is None:
        base = tempfile.mkdtemp(prefix="litmus-mcp-sdk-cli-")
        for sub in ("config", "cache"):
            os.makedirs(os.path.join(base, sub), exist_ok=True)
        _isolated_dir = base
    return _isolated_dir


def _resolve_cli_binary() -> str:
    explicit = os.getenv("LITMUS_CLI_PATH")
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=(
                    f"LITMUS_CLI_PATH is set to '{explicit}' but it is not "
                    "an executable file"
                ),
            )
        )
    found = shutil.which("litmus-cli")
    if found:
        return found
    # Transitional fallback: accept a pre-rename binary already on PATH.
    found = shutil.which("litmus-sdk-cli")
    if found:
        logger.warning(
            "Using deprecated 'litmus-sdk-cli' binary found on PATH; it was "
            "renamed to 'litmus-cli' and the fallback will be removed"
        )
        return found
    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message=(
                "litmus-cli binary not found. Install it from the cli-v* "
                f"releases at {_RELEASES_URL} and put it on PATH, or set "
                "LITMUS_CLI_PATH to its location."
            ),
        )
    )


def _build_cli_env(request: Request) -> dict:
    """Build the subprocess environment from request headers.

    Deliberately does NOT inherit os.environ: only the forwarded connection
    headers and the isolation/cache paths reach the CLI.
    """
    base = _get_isolated_dir()
    cache = os.path.join(base, "cache")
    env = {
        "LITMUS_CONFIG_DIR": os.path.join(base, "config"),
        "LITMUS_DEVICEHUB_CACHE_DIR": os.path.join(cache, "devicehub"),
        "LITMUS_ANALYTICS_CACHE_DIR": os.path.join(cache, "analytics"),
        "LITMUS_INTEGRATIONS_CACHE_DIR": os.path.join(cache, "integrations"),
    }
    for key in _FORWARDED_HEADERS:
        value = request.headers.get(key)
        if value:
            env[key] = value
    # Mirror get_litmus_connection: prefer the LEM bridge when all four bridge
    # credentials are present, unless the client set USE_LEM_BRIDGE itself.
    if "USE_LEM_BRIDGE" not in env and all(
        env.get(k) for k in _LEM_BRIDGE_HEADERS
    ):
        env["USE_LEM_BRIDGE"] = "true"
    return env


async def _run_cli(argv: list, env: dict) -> tuple:
    """Run the CLI with the given argv and env; return (rc, stdout, stderr)."""
    binary = _resolve_cli_binary()
    process = await asyncio.create_subprocess_exec(
        binary,
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_CLI_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=f"litmus-cli timed out after {_CLI_TIMEOUT_SECONDS}s",
            )
        )
    return (
        process.returncode,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def discover_litmus_sdk_functions(
    request: Request, arguments: dict | None = None
) -> list[TextContent]:
    """Browse the SDK function catalog via `litmus-cli list [prefix]`."""
    prefix = (arguments or {}).get("prefix", "")
    argv = ["list"] + ([prefix] if prefix else [])
    try:
        returncode, stdout, stderr = await _run_cli(argv, _build_cli_env(request))
        if returncode != 0:
            return format_error_response(
                "sdk_discover_failed", (stderr or stdout).strip()
            )
        logger.info(f"litmus-cli list {prefix or '(all)'} succeeded")
        return format_success_response(
            {"prefix": prefix or None, "functions": stdout.strip()}
        )
    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error running litmus-cli list: {e}", exc_info=True)
        return format_error_response("sdk_discover_failed", str(e))


async def call_litmus_sdk_function(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Invoke one SDK function via `litmus-cli run`, gated on approval."""
    arguments = arguments or {}
    function = arguments.get("function")
    if not function or not isinstance(function, str):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    "'function' parameter is required: a dotted path exactly as "
                    "returned by litmus_sdk_discover (e.g. 'le.devicehub.ListDevices')"
                ),
            )
        )
    if arguments.get("user_approved") is not True:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    f"Refusing to run '{function}': this tool requires explicit "
                    "user approval for every call. Show the user the exact "
                    "function and arguments, ask for approval, and retry with "
                    "user_approved=true only after they agree."
                ),
            )
        )
    function_args = arguments.get("args") or {}
    if not isinstance(function_args, dict):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="'args' must be a JSON object keyed by SDK parameter names",
            )
        )

    argv = ["run", function]
    if function_args:
        argv += ["--args", json.dumps(function_args)]
    try:
        returncode, stdout, stderr = await _run_cli(argv, _build_cli_env(request))
        if returncode != 0:
            return format_error_response("sdk_call_failed", (stderr or stdout).strip())

        try:
            result = json.loads(stdout)
        except ValueError:
            result = stdout.strip()

        logger.info(f"litmus-cli run {function} succeeded")
        return format_success_response({"function": function, "result": result})
    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error running litmus-cli run {function}: {e}", exc_info=True)
        return format_error_response("sdk_call_failed", str(e))


TOOLS = [
    {
        "name": "litmus_sdk_discover",
        "category": "sdk.fallback",
        "description": (
            "Browses the full Litmus SDK function catalog (~550 functions). Litmus "
            "Edge packages live under the 'le.' prefix (le.devicehub, le.analytics, "
            "le.digitaltwins, le.flows, le.integrations, le.marketplace, le.opc, "
            "le.system); lem.* and unify.* are top-level. Use this ONLY when no "
            "dedicated tool covers the operation, to find a function for "
            "litmus_sdk_call. Optionally pass a dotted-path prefix (e.g. "
            "'le.integrations' or 'lem.Get') to narrow the listing. Returned dotted "
            "paths and parameter names are exactly what litmus_sdk_call expects; do "
            "not guess paths that this tool did not return."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "prefix": {
                    "type": "string",
                    "description": (
                        "Optional dotted-path prefix to filter the catalog "
                        "(e.g. 'le.devicehub', 'le.integrations', 'lem.Get')"
                    ),
                },
            },
            "required": [],
        },
        "annotations": ToolAnnotations(title="Discover SDK Functions", readOnlyHint=True),
        "handler": discover_litmus_sdk_functions,
    },
    {
        "name": "litmus_sdk_call",
        "category": "sdk.fallback",
        "description": (
            "FALLBACK - POTENTIALLY DESTRUCTIVE. Invokes any Litmus SDK function "
            "by dotted path via the litmus-cli dispatcher. Use ONLY when no "
            "dedicated tool covers the operation, with a path returned by "
            "litmus_sdk_discover. Many SDK functions modify or delete device "
            "configuration (create/update/delete/restart/deploy) with no undo. "
            "APPROVAL REQUIRED: every call fails unless user_approved=true, and "
            "you may set user_approved=true ONLY after showing the user the exact "
            "function and arguments and receiving their explicit approval in this "
            "conversation. Never set it preemptively."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": (
                        "Dotted function path exactly as returned by "
                        "litmus_sdk_discover (e.g. 'le.devicehub.ListDevices')"
                    ),
                },
                "args": {
                    "type": "object",
                    "description": (
                        "Function arguments as a JSON object keyed by the SDK "
                        "parameter names shown by litmus_sdk_discover"
                    ),
                },
                "user_approved": {
                    "type": "boolean",
                    "description": (
                        "Must be true. Only set after the user explicitly approved "
                        "this exact function call and its arguments."
                    ),
                },
            },
            "required": ["function", "user_approved"],
        },
        "annotations": ToolAnnotations(
            title="Call SDK Function", destructiveHint=True, readOnlyHint=False
        ),
        "handler": call_litmus_sdk_function,
    },
]
