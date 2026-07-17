from typing import Any, Optional
from starlette.requests import Request
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR
import logging

from urllib.parse import urlparse

from litmussdk.utils.conn import (
    new_le_connection,
    new_lem_bridge_connection,
    new_lem_connection,
)
from config import DEFAULT_TIMEOUT, NATS_PORT

logger = logging.getLogger(__name__)


def get_litmus_connection(request: Request) -> Any:
    """
    Get Litmus SDK connection from request headers.

    Supports two connection modes:
    - LEM bridge: when EDGE_MANAGER_URL header is present
    - Direct: when EDGE_URL + OAuth credentials are present

    Args:
        request: Starlette request object

    Returns:
        Litmus SDK connection instance

    Raises:
        McpError: If authentication fails
    """
    validate_certificate = (
        request.headers.get("VALIDATE_CERTIFICATE", "false").lower() == "true"
    )

    manager_url = request.headers.get("EDGE_MANAGER_URL", "")
    api_token = request.headers.get("EDGE_API_TOKEN", "")
    project_id = request.headers.get("EDGE_MANAGER_PROJECT_ID", "")
    device_id = request.headers.get("EDGE_MANAGER_DEVICE_ID", "")
    if manager_url and api_token and project_id and device_id:
        # LEM bridge path — all four credentials present
        try:
            connection = new_lem_bridge_connection(
                edge_manager_url=manager_url,
                edge_api_token=api_token,
                project_id=project_id,
                device_id=device_id,
                validate_certificate=validate_certificate,
                timeout_seconds=DEFAULT_TIMEOUT,
            )
            logger.debug(f"LEM bridge connection established to {manager_url}")
            return connection
        except Exception as e:
            logger.error(f"LEM bridge connection failed: {e}", exc_info=True)
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Failed to connect via LEM bridge: {str(e)}",
                )
            ) from e

    # Direct connection path
    edge_url = request.headers.get("EDGE_URL")
    client_id = request.headers.get("EDGE_API_CLIENT_ID")
    client_secret = request.headers.get("EDGE_API_CLIENT_SECRET")
    if not edge_url and manager_url and api_token:
        # LEM credentials are configured but the bridge target is incomplete:
        # steer the model toward per-call bridge targeting instead of the
        # generic "EDGE_URL header is required" dead end.
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    "This tool targets a Litmus Edge device, but only LEM "
                    "credentials are configured. Pass 'project_id' and "
                    "'device_id' arguments to route this call to a managed "
                    "edge through the LEM bridge (find ids with "
                    "lem_list_devices), or set EDGE_MANAGER_PROJECT_ID and "
                    "EDGE_MANAGER_DEVICE_ID headers, or configure EDGE_URL "
                    "with EDGE_API_CLIENT_ID/EDGE_API_CLIENT_SECRET for a "
                    "direct connection."
                ),
            )
        )
    _validate_auth_headers(edge_url, client_id, client_secret)
    try:
        connection = new_le_connection(
            edge_url=edge_url,
            client_id=client_id,
            client_secret=client_secret,
            validate_certificate=validate_certificate,
            timeout_seconds=DEFAULT_TIMEOUT,
        )
        logger.debug(f"Connection established to {edge_url}")
        return connection
    except Exception as e:
        logger.error(f"Connection failed: {e}", exc_info=True)
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=f"Failed to connect to Litmus Edge: {str(e)}",
            )
        ) from e


def _default_admin_url(manager_url: str) -> str:
    """Derive the LEM admin URL from the manager URL by replacing the port with 8446."""
    raw = manager_url if "://" in manager_url else f"https://{manager_url}"
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or manager_url.split("/")[0].split(":")[0]
    return f"{scheme}://{host}:8446"


def get_lem_connection(request: Request) -> Any:
    """
    Build a Litmus Edge Manager (LEM) SDK connection from request headers.

    Required headers:
      - EDGE_MANAGER_URL: LEM cloud URL
      - EDGE_API_TOKEN:   LEM API token (sent as X-AuthToken)

    Optional headers:
      - EDGE_MANAGER_ADMIN_URL: defaults to EDGE_MANAGER_URL host with port 8446
      - VALIDATE_CERTIFICATE:   defaults to false
    """
    manager_url = request.headers.get("EDGE_MANAGER_URL", "")
    api_token = request.headers.get("EDGE_API_TOKEN", "")
    admin_url = request.headers.get("EDGE_MANAGER_ADMIN_URL", "")
    validate_certificate = (
        request.headers.get("VALIDATE_CERTIFICATE", "false").lower() == "true"
    )

    if not manager_url:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS, message="EDGE_MANAGER_URL header is required"
            )
        )
    if not api_token:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS, message="EDGE_API_TOKEN header is required"
            )
        )

    if not admin_url:
        admin_url = _default_admin_url(manager_url)

    try:
        connection = new_lem_connection(
            edge_manager_url=manager_url,
            edge_manager_admin_url=admin_url,
            edge_api_token=api_token,
            validate_certificate=validate_certificate,
            timeout_seconds=DEFAULT_TIMEOUT,
        )
        logger.debug(f"LEM connection established to {manager_url}")
        return connection
    except Exception as e:
        logger.error(f"LEM connection failed: {e}", exc_info=True)
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=f"Failed to connect to Litmus Edge Manager: {str(e)}",
            )
        ) from e


def get_lem_project_id(request: Request, arguments: dict | None) -> str:
    """
    Resolve the LEM project_id from tool arguments, falling back to the
    EDGE_MANAGER_PROJECT_ID header.
    """
    project_id = (arguments or {}).get("project_id") or request.headers.get(
        "EDGE_MANAGER_PROJECT_ID", ""
    )
    if not project_id:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    "'project_id' is required (pass it as a tool argument or set the "
                    "EDGE_MANAGER_PROJECT_ID header)"
                ),
            )
        )
    return project_id


def _validate_auth_headers(edge_url: str, client_id: str, client_secret: str) -> None:
    """Validate required authentication headers."""
    if not edge_url:
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message="EDGE_URL header is required")
        )
    if not client_id:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS, message="EDGE_API_CLIENT_ID header is required"
            )
        )
    if not client_secret:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS, message="EDGE_API_CLIENT_SECRET header is required"
            )
        )


def _data_plane_host(raw: Optional[str]) -> Optional[str]:
    """Extract the bare hostname from an address such as
    'https://edge.example.com:8443', '10.0.0.5:443', or 'edge.example.com/'.
    Returns None when nothing parseable was given."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        return urlparse(raw).hostname or None
    except ValueError:
        return None


def get_nats_connection_params(request: Optional[Request] = None) -> dict:
    """
    Get NATS connection parameters from request headers.

    Host resolution: an explicit NATS_SOURCE header wins; otherwise the host
    is derived from EDGE_URL (scheme, port, and path stripped) and the
    returned dict carries derived_from_edge_url=True so tools can tell the
    caller a fallback was used.

    Returns:
        Dictionary containing NATS connection parameters:
        - nats_source: NATS server address
        - nats_port: NATS server port (default 4222)
        - nats_user: Optional username (legacy; the LE broker ignores it)
        - nats_password: Access-account API key (the only credential LE checks)
        - nats_token: Optional alias for nats_password
        - use_tls: Whether to use TLS/SSL
        - derived_from_edge_url: True when the host came from EDGE_URL

    Raises:
        McpError: If neither NATS_SOURCE nor EDGE_URL yields a host
    """
    if request is None:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="NATS connection requires a request context",
            )
        )

    nats_source = _data_plane_host(request.headers.get("NATS_SOURCE"))
    derived_from_edge_url = False
    if not nats_source:
        nats_source = _data_plane_host(request.headers.get("EDGE_URL"))
        derived_from_edge_url = nats_source is not None

    if not nats_source:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    "The NATS broker host is not configured. Set EDGE_URL "
                    "(the NATS host is derived from it) or NATS_SOURCE in the "
                    "MCP configuration. Tell the user one of the two is "
                    "required for live data tools."
                ),
            )
        )

    nats_port = request.headers.get("NATS_PORT") or NATS_PORT
    nats_user = request.headers.get("NATS_USER")
    nats_password = request.headers.get("NATS_PASSWORD")
    nats_token = request.headers.get("NATS_TOKEN")
    use_tls_str = request.headers.get("NATS_TLS", "true")
    use_tls = use_tls_str.lower() in ("true", "1", "yes")

    params = {
        "nats_source": nats_source,
        "nats_port": nats_port,
        "nats_user": nats_user,
        "nats_password": nats_password,
        "nats_token": nats_token,
        "use_tls": use_tls,
        "derived_from_edge_url": derived_from_edge_url,
    }

    logger.debug(
        f"NATS connection params: source={nats_source}, port={nats_port}, "
        f"use_tls={use_tls}, derived_from_edge_url={derived_from_edge_url}"
    )

    return params


def get_influx_connection_params(request: Request) -> dict:
    """
    Get InfluxDB connection parameters from request headers.

    Host resolution: an explicit INFLUX_HOST header wins; otherwise the host
    is derived from EDGE_URL (scheme, port, and path stripped) and the
    returned dict carries derived_from_edge_url=True so tools can tell the
    caller a fallback was used.

    Returns:
        Dictionary containing InfluxDB connection parameters:
        - INFLUX_HOST: InfluxDB server address
        - INFLUX_PORT: InfluxDB server port (default 8086)
        - INFLUX_USERNAME: Username for authentication
        - INFLUX_PASSWORD: Password for authentication
        - INFLUX_DB_NAME: Database name
        - derived_from_edge_url: True when the host came from EDGE_URL

    Raises:
        McpError: If required parameters are missing
    """
    influx_host = _data_plane_host(request.headers.get("INFLUX_HOST"))
    derived_from_edge_url = False
    if not influx_host:
        influx_host = _data_plane_host(request.headers.get("EDGE_URL"))
        derived_from_edge_url = influx_host is not None

    influx_port = request.headers.get("INFLUX_PORT", "8086")
    influx_username = request.headers.get("INFLUX_USERNAME")
    influx_password = request.headers.get("INFLUX_PASSWORD")
    influx_db_name = request.headers.get("INFLUX_DB_NAME", "tsdata")

    if not influx_host:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    "The InfluxDB host is not configured. Set EDGE_URL "
                    "(the InfluxDB host is derived from it) or INFLUX_HOST in "
                    "the MCP configuration. Tell the user one of the two is "
                    "required for historical data tools."
                ),
            )
        )

    if not influx_username:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="INFLUX_USERNAME header is required",
            )
        )

    if not influx_password:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="INFLUX_PASSWORD header is required",
            )
        )

    params = {
        "INFLUX_HOST": influx_host,
        "INFLUX_PORT": int(influx_port),
        "INFLUX_USERNAME": influx_username,
        "INFLUX_PASSWORD": influx_password,
        "INFLUX_DB_NAME": influx_db_name,
        "derived_from_edge_url": derived_from_edge_url,
    }

    logger.debug(
        f"InfluxDB connection params: host={influx_host}, port={influx_port}, "
        f"db={influx_db_name}, derived_from_edge_url={derived_from_edge_url}"
    )

    return params
