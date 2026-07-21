import logging
import os
import ssl

logger = logging.getLogger(__name__)

NATS_PORT = "4222"
MCP_PORT = 8000
DEFAULT_TIMEOUT = 600

# Disable STDIO by default
ENABLE_STDIO = os.getenv("ENABLE_STDIO", "false").lower() in ("true", "1", "yes")


def ssl_config():
    """Configure SSL context for NATS connections"""
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    return ssl_ctx


def server_version():
    """Project version from pyproject.toml; the project is a uv virtual
    project (not an installed distribution), so package metadata is absent."""
    from pathlib import Path

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        import tomllib

        with pyproject.open("rb") as f:
            return tomllib.load(f)["project"]["version"]
    except Exception:
        return None
