import logging
import os
import ssl

logger = logging.getLogger(__name__)

NATS_SOURCE = "10.30.50.1"
NATS_PORT = "4222"
MCP_PORT = 8000
DEFAULT_TIMEOUT = 600
ENABLE_STDIO = os.getenv("ENABLE_STDIO", "false").lower() in ("false", "0", "no")


def ssl_config():
    """Configure SSL context for NATS connections"""
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    return ssl_ctx
