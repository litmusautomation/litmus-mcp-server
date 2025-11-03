from typing import Any, Optional
from starlette.requests import Request
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR
import logging

from litmussdk.utils.conn import new_le_connection
from config import DEFAULT_TIMEOUT, NATS_PORT

logger = logging.getLogger(__name__)


def get_litmus_connection(request: Request) -> Any:
    """
    Get Litmus SDK connection from request headers.

    Args:
        request: Starlette request object

    Returns:
        Litmus SDK connection instance

    Raises:
        McpError: If authentication fails
    """
    # Extract headers
    edge_url = request.headers.get("EDGE_URL")
    client_id = request.headers.get("EDGE_API_CLIENT_ID")
    client_secret = request.headers.get("EDGE_API_CLIENT_SECRET")
    validate_certificate = (
        request.headers.get("VALIDATE_CERTIFICATE", "false").lower() == "true"
    )

    # Validate required headers
    _validate_auth_headers(edge_url, client_id, client_secret)

    # Create connection
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


def get_nats_connection_params(request: Optional[Request] = None) -> dict:
    """
    Get NATS connection parameters from request headers or environment variables.

    Priority order:
    1. Request headers (if request is provided)
    2. Environment variables

    Args:
        request: Optional Starlette request object

    Returns:
        Dictionary containing NATS connection parameters:
        - nats_source: NATS server address
        - nats_port: NATS server port
        - nats_user: Optional username for authentication
        - nats_password: Optional password for authentication
        - nats_token: Optional token for authentication
        - use_tls: Whether to use TLS/SSL

    Raises:
        McpError: If required parameters are missing
    """
    # Try to get from request headers first, then fall back to environment variables

    nats_source = request.headers.get("NATS_SOURCE") or request.headers.get("EDGE_URL")
    nats_port = request.headers.get("NATS_PORT") or NATS_PORT
    nats_user = request.headers.get("NATS_USER")
    nats_password = request.headers.get("NATS_PASSWORD")
    nats_token = request.headers.get("NATS_TOKEN")
    use_tls_str = request.headers.get("NATS_TLS", "true")

    # Validate required parameters
    if not nats_source:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="NATS_SOURCE is required (from header or environment variable)",
            )
        )

    if not nats_port:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="NATS_PORT is required (from header or environment variable)",
            )
        )

    # Clean up NATS source - remove http/https protocol if present
    if nats_source:
        nats_source = nats_source.replace("https://", "").replace("http://", "")
        # Remove trailing slash if present
        nats_source = nats_source.rstrip("/")

    # Convert use_tls to boolean
    use_tls = use_tls_str.lower() in ("true", "1", "yes")

    params = {
        "nats_source": nats_source,
        "nats_port": nats_port,
        "nats_user": nats_user,
        "nats_password": nats_password,
        "nats_token": nats_token,
        "use_tls": use_tls,
    }

    logger.debug(
        f"NATS connection params: source={nats_source}, port={nats_port}, use_tls={use_tls}"
    )

    return params


def get_influx_connection_params(request: Request) -> dict:
    """
    Get InfluxDB connection parameters from request headers.

    Args:
        request: Starlette request object

    Returns:
        Dictionary containing InfluxDB connection parameters:
        - INFLUX_HOST: InfluxDB server address
        - INFLUX_PORT: InfluxDB server port
        - INFLUX_USERNAME: Username for authentication
        - INFLUX_PASSWORD: Password for authentication
        - INFLUX_DB_NAME: Database name

    Raises:
        McpError: If required parameters are missing
    """
    influx_host = request.headers.get("INFLUX_HOST") or request.headers.get("EDGE_URL")
    influx_port = request.headers.get("INFLUX_PORT", "8086")
    influx_username = request.headers.get("INFLUX_USERNAME")
    influx_password = request.headers.get("INFLUX_PASSWORD")
    influx_db_name = request.headers.get("INFLUX_DB_NAME", "tsdata")

    # Validate required parameters
    if not influx_host:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="INFLUX_HOST header is required",
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

    # Clean up InfluxDB host - ensure it has http/https protocol
    if influx_host:
        # Remove trailing slash if present
        influx_host = influx_host.rstrip("/")

    params = {
        "INFLUX_HOST": influx_host,
        "INFLUX_PORT": int(influx_port),
        "INFLUX_USERNAME": influx_username,
        "INFLUX_PASSWORD": influx_password,
        "INFLUX_DB_NAME": influx_db_name,
    }

    logger.debug(
        f"InfluxDB connection params: host={influx_host}, port={influx_port}, db={influx_db_name}"
    )

    return params
