"""
Generic SDK fallback tools backed by the standalone `litmus-cli` Go binary.

The curated tools in the other modules cover the common workflows. These
expose the full generated SDK surface (~550 functions) for everything else:

  - litmus_sdk_discover  ->  `litmus-cli list [prefix]`
  - litmus_sdk_read      ->  `litmus-cli run <dotted.path>` (read-only functions)
  - litmus_sdk_write     ->  `litmus-cli run <dotted.path>` (everything else)

The read/write split is by the verb prefix of the function's final path
segment (_READ_VERBS). Functions that don't match a read verb are treated
as writes, so misclassification can only add an approval prompt, never
skip one.

Connection credentials are forwarded from the request headers to the
subprocess environment. The header names used by this server and the CLI's
environment variables are intentionally identical, so forwarding is verbatim.
`LITMUS_CONFIG_DIR` is pointed at an isolated per-process directory so a
profile saved on the host can never leak into a request, and no request can
write one.

`litmus_sdk_write` is approval-gated: it fails unless `user_approved` is true,
which the model may only set after the user explicitly approved the exact
function and arguments in conversation.
"""

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path
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


# ── binary resolution and self-bootstrap ─────────────────────────────────────
#
# Docker installs the binary at build time and run.sh bootstraps it for local
# runs. A bare `python src/server.py` has neither, so as a last resort the
# server downloads the pinned release itself on first use (checksum-verified,
# same release the Dockerfile pins) instead of hard-failing the CLI-backed
# tools.

_FALLBACK_CLI_VERSION = "cli-v0.7.0"  # used only when Dockerfile is absent

_BOOTSTRAP_BASE_DIR = Path.home() / ".cache" / "litmus-mcp-server" / "bin"

_DOWNLOAD_TIMEOUT_SECONDS = 60

_bootstrap_lock: Optional[asyncio.Lock] = None


def _pinned_cli_version() -> str:
    """Release tag to install: LITMUS_CLI_VERSION env, else the Dockerfile
    ARG (single source of truth), else the baked-in fallback."""
    env = os.getenv("LITMUS_CLI_VERSION")
    if env:
        return env
    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    try:
        for line in dockerfile.read_text().splitlines():
            if line.startswith("ARG LITMUS_CLI_VERSION="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    except OSError:
        pass
    return _FALLBACK_CLI_VERSION


def _cli_asset_name() -> str:
    system = platform.system()
    os_name = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(system)
    if os_name is None:
        raise RuntimeError(f"unsupported OS '{system}' for litmus-cli bootstrap")
    machine = platform.machine().lower()
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }.get(machine)
    if arch is None:
        raise RuntimeError(
            f"unsupported architecture '{machine}' for litmus-cli bootstrap"
        )
    suffix = ".exe" if os_name == "windows" else ""
    return f"litmus-cli-{os_name}-{arch}{suffix}"


def _bootstrap_target(tag: Optional[str] = None) -> Path:
    """Version-scoped install path, so a pin bump re-downloads naturally."""
    name = "litmus-cli.exe" if platform.system() == "Windows" else "litmus-cli"
    return _BOOTSTRAP_BASE_DIR / (tag or _pinned_cli_version()) / name


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:
        return resp.read()


def version_key(text: str) -> tuple:
    """Numeric sort key for version-ish strings ('cli-v0.8.0', 'v.1.1.1')."""
    return tuple(int(x) for x in re.findall(r"\d+", text or ""))


def get_latest_cli_tag() -> Optional[str]:
    """Newest cli-v* release tag from the litmus-sdk-releases repo, or None
    when none are visible. Blocking; run in a thread."""
    api_url = (
        "https://api.github.com/repos/litmusautomation/litmus-sdk-releases"
        "/releases?per_page=30"
    )
    releases = json.loads(_fetch(api_url))
    tags = [
        r.get("tag_name")
        for r in releases
        if isinstance(r, dict) and str(r.get("tag_name", "")).startswith("cli-v")
    ]
    return max(tags, key=version_key) if tags else None


async def upgrade_cli_binary() -> tuple:
    """Install the newest litmus-cli release (checksum-verified) and make
    this server process use it from now on. Returns (tag, path)."""
    tag = await asyncio.to_thread(get_latest_cli_tag)
    if not tag:
        raise RuntimeError("no cli-v* releases found on litmus-sdk-releases")
    path = await asyncio.to_thread(_bootstrap_cli_binary, tag)
    # Later resolves honor these overrides (LITMUS_CLI_PATH has top
    # precedence), so the upgraded binary wins for the lifetime of the
    # process; a restart reverts to the configured pin/path.
    os.environ["LITMUS_CLI_VERSION"] = tag
    os.environ["LITMUS_CLI_PATH"] = path
    logger.info(f"litmus-cli upgraded to {tag} at {path}")
    return tag, path


def _bootstrap_cli_binary(tag: Optional[str] = None) -> str:
    """Download a litmus-cli release (the pin by default), verify its SHA256
    against the release's SHA256SUMS, and install it under the user cache
    dir. Blocking; run in a thread."""
    tag = tag or _pinned_cli_version()
    asset = _cli_asset_name()
    target = _bootstrap_target(tag)
    if target.is_file() and os.access(target, os.X_OK):
        return str(target)

    base = f"{_RELEASES_URL}/download/{tag}"
    logger.info(f"Installing litmus-cli {tag} ({asset}) to {target}")
    sums = _fetch(f"{base}/SHA256SUMS").decode()
    binary = _fetch(f"{base}/{asset}")

    expected = None
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == asset:
            expected = parts[0]
            break
    if not expected:
        raise RuntimeError(f"no SHA256SUMS entry for {asset} in release {tag}")
    actual = hashlib.sha256(binary).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"checksum mismatch for {asset}: expected {expected}, got {actual}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(binary)
    tmp.chmod(0o755)
    os.replace(tmp, target)
    return str(target)


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
    # A binary installed by a previous self-bootstrap.
    bootstrapped = _bootstrap_target()
    if bootstrapped.is_file() and os.access(bootstrapped, os.X_OK):
        return str(bootstrapped)
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


async def _ensure_cli_binary() -> str:
    """Resolve the CLI binary, self-installing the pinned release if nothing
    is found."""
    global _bootstrap_lock
    try:
        return _resolve_cli_binary()
    except McpError:
        pass
    if _bootstrap_lock is None:
        _bootstrap_lock = asyncio.Lock()
    async with _bootstrap_lock:
        # Another call may have finished the install while we waited.
        try:
            return _resolve_cli_binary()
        except McpError:
            pass
        try:
            return await asyncio.to_thread(_bootstrap_cli_binary)
        except Exception as e:
            logger.error(f"litmus-cli self-install failed: {e}")
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=(
                        f"litmus-cli binary not found and automatic install "
                        f"failed ({e}). Install it from the cli-v* releases "
                        f"at {_RELEASES_URL} and put it on PATH, or set "
                        "LITMUS_CLI_PATH to its location."
                    ),
                )
            ) from e


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
    # Mirror get_litmus_connection's default of not validating certificates.
    # Without this, litmussdk's env default (True) applies inside the CLI and
    # requests to edges with self-signed certs fail.
    env.setdefault("VALIDATE_CERTIFICATE", "false")
    return env


async def _run_cli(argv: list, env: dict) -> tuple:
    """Run the CLI with the given argv and env; return (rc, stdout, stderr)."""
    binary = await _ensure_cli_binary()
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


# Read-only SDK functions are recognized by the verb prefix of the final
# path segment. Anything else is a write and must go through the
# litmus_sdk_write approval gate.
_READ_VERBS = (
    "Get",
    "List",
    "Browse",
    "Describe",
    "Read",
    "Search",
    "Find",
    "Query",
    "Count",
)


def _is_read_function(function: str) -> bool:
    leaf = function.rsplit(".", 1)[-1]
    return any(
        leaf.startswith(verb)
        and (len(leaf) == len(verb) or leaf[len(verb)].isupper())
        for verb in _READ_VERBS
    )


def _require_function(arguments: dict) -> str:
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
    return function


def _require_args(arguments: dict) -> dict:
    function_args = arguments.get("args") or {}
    if not isinstance(function_args, dict):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="'args' must be a JSON object keyed by SDK parameter names",
            )
        )
    return function_args


class CLIFunctionError(Exception):
    """A litmus-cli `run` invocation exited non-zero."""

    def __init__(self, function: str, message: str):
        self.function = function
        super().__init__(message)


async def run_cli_function(request: Request, function: str, function_args: dict):
    """Invoke one SDK function via `litmus-cli run` and return its decoded
    JSON result (or raw stdout when the output is not JSON).

    Shared backend for the generic litmus_sdk_read/write tools and for
    curated tools in other modules that are CLI-backed. Raises
    CLIFunctionError on a non-zero exit and McpError when the binary is
    missing or times out.
    """
    argv = ["run", function]
    if function_args:
        argv += ["--args", json.dumps(function_args)]
    returncode, stdout, stderr = await _run_cli(argv, _build_cli_env(request))
    if returncode != 0:
        raise CLIFunctionError(function, (stderr or stdout).strip())
    try:
        result = json.loads(stdout)
    except ValueError:
        result = stdout.strip()
    logger.info(f"litmus-cli run {function} succeeded")
    return result


async def _run_sdk_function(
    request: Request, function: str, function_args: dict, error_code: str
) -> list[TextContent]:
    try:
        result = await run_cli_function(request, function, function_args)
        return format_success_response({"function": function, "result": result})
    except CLIFunctionError as e:
        return format_error_response(error_code, str(e))
    except McpError:
        raise
    except Exception as e:
        logger.error(f"Error running litmus-cli run {function}: {e}", exc_info=True)
        return format_error_response(error_code, str(e))


async def read_litmus_sdk_function(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Invoke one read-only SDK function via `litmus-cli run`."""
    arguments = arguments or {}
    function = _require_function(arguments)
    if not _is_read_function(function):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    f"'{function}' is not a read-only SDK function (read-only "
                    f"functions start with one of: {', '.join(_READ_VERBS)}). "
                    "Use litmus_sdk_write, which requires explicit user approval."
                ),
            )
        )
    return await _run_sdk_function(
        request, function, _require_args(arguments), "sdk_read_failed"
    )


async def write_litmus_sdk_function(
    request: Request, arguments: dict
) -> list[TextContent]:
    """Invoke one state-changing SDK function via `litmus-cli run`, gated on
    approval."""
    arguments = arguments or {}
    function = _require_function(arguments)
    if _is_read_function(function):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    f"'{function}' is a read-only SDK function; call it via "
                    "litmus_sdk_read instead (no approval required)."
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
    return await _run_sdk_function(
        request, function, _require_args(arguments), "sdk_call_failed"
    )


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
            "litmus_sdk_read or litmus_sdk_write. Optionally pass a dotted-path "
            "prefix (e.g. 'le.integrations' or 'lem.Get') to narrow the listing. "
            "Returned dotted paths and parameter names are exactly what "
            "litmus_sdk_read and litmus_sdk_write expect; do not guess paths that "
            "this tool did not return. SDK reference: "
            "https://docs.litmus.io/litmus-mcp-server"
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
        "name": "litmus_sdk_read",
        "category": "sdk.fallback",
        "description": (
            "FALLBACK - READ-ONLY. Invokes a single read-only Litmus SDK function "
            "by dotted path via the litmus-cli dispatcher (SDK reference: "
            "https://docs.litmus.io/litmus-mcp-server). Only functions whose final "
            "path segment starts with Get, List, Browse, Describe, Read, Search, "
            "Find, Query, or Count are accepted; anything else is rejected and "
            "must go through litmus_sdk_write. Use ONLY when no dedicated tool "
            "covers the operation, with a path returned by litmus_sdk_discover."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": (
                        "Dotted read-only function path exactly as returned by "
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
            },
            "required": ["function"],
        },
        "annotations": ToolAnnotations(title="Read SDK Function", readOnlyHint=True),
        "handler": read_litmus_sdk_function,
    },
    {
        "name": "litmus_sdk_write",
        "category": "sdk.fallback",
        "description": (
            "FALLBACK - POTENTIALLY DESTRUCTIVE. Invokes a state-changing Litmus "
            "SDK function by dotted path via the litmus-cli dispatcher (SDK "
            "reference: https://docs.litmus.io/litmus-mcp-server). Use ONLY when "
            "no dedicated tool covers the operation, with a path returned by "
            "litmus_sdk_discover. Many SDK functions modify or delete device "
            "configuration (create/update/delete/restart/deploy) with no undo. "
            "Read-only functions are rejected; call them via litmus_sdk_read. "
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
                        "Dotted state-changing function path exactly as returned "
                        "by litmus_sdk_discover (e.g. 'le.devicehub.DeleteDevice')"
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
            title="Write SDK Function", destructiveHint=True, readOnlyHint=False
        ),
        "handler": write_litmus_sdk_function,
    },
]
