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


def tls_settings() -> dict:
    """Native TLS for the HTTP transports, from SSL_CERTFILE / SSL_KEYFILE
    environment variables (plain process env, like ENABLE_STDIO; not .env).

    Returns uvicorn ssl kwargs when both are set, {} when neither is.
    Partial or unreadable configuration raises instead of falling back to
    plain HTTP, which the operator would mistake for HTTPS.
    """
    certfile = os.getenv("SSL_CERTFILE", "").strip()
    keyfile = os.getenv("SSL_KEYFILE", "").strip()
    if not certfile and not keyfile:
        return {}
    if not (certfile and keyfile):
        raise ValueError(
            "SSL_CERTFILE and SSL_KEYFILE must be set together to enable TLS "
            f"(got SSL_CERTFILE={certfile!r}, SSL_KEYFILE={keyfile!r})"
        )
    for name, path in (("SSL_CERTFILE", certfile), ("SSL_KEYFILE", keyfile)):
        if not os.path.isfile(path):
            raise ValueError(f"{name} is not a readable file: {path}")
    kwargs = {"ssl_certfile": certfile, "ssl_keyfile": keyfile}
    password = os.getenv("SSL_KEYFILE_PASSWORD", "")
    if password:
        kwargs["ssl_keyfile_password"] = password
    return kwargs


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
