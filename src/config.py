import logging
import os
import ssl

logger = logging.getLogger(__name__)

NATS_PORT = "4222"
MCP_PORT = 8000
DEFAULT_TIMEOUT = 600
ENABLE_STDIO = os.getenv("ENABLE_STDIO", "true").lower() in ("true", "1", "yes")


def ssl_config():
    """Configure SSL context for NATS connections"""
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    return ssl_ctx
