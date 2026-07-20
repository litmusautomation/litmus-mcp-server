import json
import re
from mcp.types import TextContent


def format_success_response(data: dict) -> list[TextContent]:
    """Format a successful response."""
    result = {"success": True, **data}
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


def format_error_response(error_code: str, message: str) -> list[TextContent]:
    """Format an error response."""
    result = {"success": False, "error": error_code, "message": message}
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# ── secret redaction ─────────────────────────────────────────────────────────

REDACTED = "[REDACTED]"

_SECRET_KEY_RE = re.compile(
    r"password|passwd|secret|private[_-]?key|api[_-]?key|apikey|token"
    r"|activation[_-]?code|credential",
    re.IGNORECASE,
)

# Keys that merely reference a secret without containing one
# (tokenExpiry, passwordUpdatedAt, DisableEncryptedPasswordCheck, ...).
_NON_SECRET_SUFFIX_RE = re.compile(
    r"(expiry|expiration|expires|updated|created|date|time|status|type"
    r"|name|count|length|enabled|check|policy|id|url)$",
    re.IGNORECASE,
)

# Values that are obviously flags, not secrets.
_NON_SECRET_VALUES = {"true", "false"}

_PEM_PRIVATE_KEY_RE = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")


def _is_secret_key(key: str) -> bool:
    return bool(
        _SECRET_KEY_RE.search(key) and not _NON_SECRET_SUFFIX_RE.search(key)
    )


def _is_secret_value(value) -> bool:
    if value in (None, "") or isinstance(value, bool):
        return False
    return not (isinstance(value, str) and value.lower() in _NON_SECRET_VALUES)


def redact_secrets(value):
    """Recursively mask secret material in a JSON-ish structure before it is
    returned to the LLM: values under secret-looking keys (passwords, api
    keys, tokens, activation codes, private keys), Name/Value property
    entries whose Name is secret-looking, and any string containing a PEM
    private-key block."""
    if isinstance(value, dict):
        entry_key = value.get("Name") or value.get("Key")
        if (
            isinstance(entry_key, str)
            and "Value" in value
            and _is_secret_key(entry_key)
            and _is_secret_value(value.get("Value"))
        ):
            return {**value, "Value": REDACTED}
        return {
            k: (
                REDACTED
                if isinstance(k, str)
                and _is_secret_key(k)
                and _is_secret_value(v)
                else redact_secrets(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    if isinstance(value, str) and _PEM_PRIVATE_KEY_RE.search(value):
        return REDACTED
    return value
