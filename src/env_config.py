"""
Environment and configuration management.

Handles .env file I/O, credential loading, Edge instance management,
and model/API key selection.
"""

import os
import logging
from typing import Tuple
import dotenv

# ── Path constants ──────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JINJA_TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# ── Network defaults (override via .env) ───────────────────────────────────

MCP_PORT = 8000

# ── Credential module-level mirrors (set by mcp_env_loader) ────────────────

EDGE_URL = ""
EDGE_API_CLIENT_ID = ""
EDGE_API_CLIENT_SECRET = ""
VALIDATE_CERTIFICATE = ""
ANTHROPIC_KEY = ""

# ── .env key names ──────────────────────────────────────────────────────────

key_of_anthropic_api_key = "ANTHROPIC_API_KEY"
key_of_openai_api_key = "OPENAI_API_KEY"
key_of_gemini_api_key = "GEMINI_API_KEY"
MODEL_NAME_OPENAI = "openai"
MODEL_NAME_ANTHROPIC = "anthropic"
MODEL_NAME_GEMINI = "gemini"
MODEL_PREFERENCE = "PREFERRED_MODEL"
PREFERRED_MODEL_ID = "PREFERRED_MODEL_ID"
ACTIVE_EDGE_INSTANCE = "ACTIVE_EDGE_INSTANCE"
ACTIVE_LEM_CONNECTION = "ACTIVE_LEM_CONNECTION"
CLIENT_SESSION_TIMEOUT_SECONDS = "CLIENT_SESSION_TIMEOUT_SECONDS"
DEFAULT_CLIENT_SESSION_TIMEOUT_SECONDS = 60
CLIENT_SESSION_TIMEOUT_SECONDS_MIN = 5
CLIENT_SESSION_TIMEOUT_SECONDS_MAX = 600

# Keys writable through POST /api/save-settings. Anything outside this set is
# rejected with 400. Keep in sync with the inputs in templates/update_env.html.
SAVE_SETTINGS_ALLOWED_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "NATS_SOURCE",
        "NATS_PORT",
        "NATS_USER",
        "NATS_PASSWORD",
        "NATS_TLS",
        "INFLUX_HOST",
        "INFLUX_PORT",
        "INFLUX_DB_NAME",
        "INFLUX_USERNAME",
        "INFLUX_PASSWORD",
        "VALIDATE_CERTIFICATE",
        "CLIENT_SESSION_TIMEOUT_SECONDS",
    }
)


# ── .env I/O ────────────────────────────────────────────────────────────────


def mcp_env_loader():
    global EDGE_URL, EDGE_API_CLIENT_ID, EDGE_API_CLIENT_SECRET, VALIDATE_CERTIFICATE, ANTHROPIC_KEY

    dotenv_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(dotenv_path):
        dotenv.load_dotenv(dotenv_path, override=True)
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
        logging.info(
            "Did not find .env file in current working directory. Defaulting to system variables"
        )

    EDGE_URL = os.environ.get("EDGE_URL", "")
    EDGE_API_CLIENT_ID = os.environ.get("EDGE_API_CLIENT_ID", "")
    EDGE_API_CLIENT_SECRET = os.environ.get("EDGE_API_CLIENT_SECRET", "")
    VALIDATE_CERTIFICATE = os.environ.get("VALIDATE_CERTIFICATE", "false")
    ANTHROPIC_KEY = os.environ.get(key_of_anthropic_api_key, "")

    # One-time migration: lift legacy single-LEM EDGE_MANAGER_URL/EDGE_API_TOKEN
    # into a LEM_CONNECTION_1 entry. Idempotent.
    migrate_legacy_lem_settings()


def _get_env_vars(env_file, override):
    path_to_env = dotenv.find_dotenv(env_file)
    if not path_to_env:
        with open(".env", "w") as f:
            f.write("ENV=Initiate")
        path_to_env = dotenv.find_dotenv(env_file)
    dotenv.load_dotenv(path_to_env or env_file, override=override)
    env_vars = {}

    if os.path.exists(path_to_env):
        with open(path_to_env, "r") as file:
            for line in file:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    env_vars[k] = v
    return env_vars, path_to_env


def get_current_mcp_env(env_file: str = ".env"):
    return _get_env_vars(env_file, override=False)


def mcp_env_remover(key: str, env_file: str = ".env"):
    env_vars, path_to_env = _get_env_vars(env_file, override=True)

    env_vars.pop(key, None)
    with open(path_to_env, "w") as file:
        for k, v in env_vars.items():
            file.write(f"{k}={v}\n")

    print(f"Removed {key} in {env_file}")


def mcp_env_updater(key: str, value: str | bool, env_file: str = ".env"):
    env_vars, path_to_env = _get_env_vars(env_file, override=True)
    env_vars[key] = value

    with open(path_to_env, "w") as file:
        for k, v in env_vars.items():
            file.write(f"{k}={v}\n")

    print(f"Updated {key} in {env_file}")


# ── Edge instance management ────────────────────────────────────────────────


def get_edge_instances() -> list:
    """Read all EDGE_INSTANCE_{i}_* keys from os.environ. Allows gaps in numbering."""
    instances = []
    for i in range(1, 50):
        url = os.environ.get(f"EDGE_INSTANCE_{i}_URL", "")
        if not url:
            continue
        instances.append(
            {
                "index": i,
                "url": url,
                "client_id": os.environ.get(f"EDGE_INSTANCE_{i}_CLIENT_ID", ""),
                "secret": os.environ.get(f"EDGE_INSTANCE_{i}_SECRET", ""),
                "name": os.environ.get(f"EDGE_INSTANCE_{i}_NAME", f"Edge {i}"),
                "type": os.environ.get(f"EDGE_INSTANCE_{i}_TYPE", "direct"),
                "api_token": os.environ.get(f"EDGE_INSTANCE_{i}_API_TOKEN", ""),
                "project_id": os.environ.get(f"EDGE_INSTANCE_{i}_PROJECT_ID", ""),
                "device_id": os.environ.get(f"EDGE_INSTANCE_{i}_DEVICE_ID", ""),
            }
        )
    return instances


def next_edge_instance_index() -> int:
    """Return the lowest unused instance index."""
    for i in range(1, 50):
        if not os.environ.get(f"EDGE_INSTANCE_{i}_URL", ""):
            return i
    return 50


def remove_edge_instance(index: int):
    """Delete all keys for an instance index."""
    for suffix in (
        "URL",
        "CLIENT_ID",
        "SECRET",
        "NAME",
        "TYPE",
        "API_TOKEN",
        "PROJECT_ID",
        "DEVICE_ID",
    ):
        mcp_env_remover(f"EDGE_INSTANCE_{index}_{suffix}")


def activate_edge_instance(index: int):
    """Copy instance credentials to main EDGE_ vars and write ACTIVE_EDGE_INSTANCE."""
    inst_type = os.environ.get(f"EDGE_INSTANCE_{index}_TYPE", "direct")
    url = os.environ.get(f"EDGE_INSTANCE_{index}_URL", "")
    if inst_type == "lem":
        mcp_env_updater("EDGE_MANAGER_URL", url)
        mcp_env_updater(
            "EDGE_API_TOKEN", os.environ.get(f"EDGE_INSTANCE_{index}_API_TOKEN", "")
        )
        mcp_env_updater(
            "EDGE_MANAGER_PROJECT_ID",
            os.environ.get(f"EDGE_INSTANCE_{index}_PROJECT_ID", ""),
        )
        mcp_env_updater(
            "EDGE_MANAGER_DEVICE_ID",
            os.environ.get(f"EDGE_INSTANCE_{index}_DEVICE_ID", ""),
        )
        mcp_env_updater("EDGE_URL", "")
        mcp_env_updater("EDGE_API_CLIENT_ID", "")
        mcp_env_updater("EDGE_API_CLIENT_SECRET", "")
    else:
        mcp_env_updater("EDGE_URL", url)
        mcp_env_updater(
            "EDGE_API_CLIENT_ID", os.environ.get(f"EDGE_INSTANCE_{index}_CLIENT_ID", "")
        )
        mcp_env_updater(
            "EDGE_API_CLIENT_SECRET",
            os.environ.get(f"EDGE_INSTANCE_{index}_SECRET", ""),
        )
        # Preserve EDGE_MANAGER_URL / EDGE_API_TOKEN: those are owned by the
        # standalone LEM Settings panel and must survive instance switches.
        # Only project/device id are bridge-specific and get cleared.
        mcp_env_updater("EDGE_MANAGER_PROJECT_ID", "")
        mcp_env_updater("EDGE_MANAGER_DEVICE_ID", "")
    mcp_env_updater(ACTIVE_EDGE_INSTANCE, str(index))
    # If activating a bridge instance, the EDGE_MANAGER_URL/TOKEN now reflect
    # that instance, not a standalone LEM connection. Clear the active LEM
    # pointer so the home-page pill reflects "managed by edge instance".
    if inst_type == "lem":
        mcp_env_updater(ACTIVE_LEM_CONNECTION, "0")
    mcp_env_loader()


# ── LEM standalone connections ─────────────────────────────────────────────


def get_lem_connections() -> list:
    """Read all LEM_CONNECTION_{i}_* keys from os.environ. Allows gaps in numbering."""
    connections = []
    for i in range(1, 50):
        url = os.environ.get(f"LEM_CONNECTION_{i}_URL", "")
        if not url:
            continue
        connections.append(
            {
                "index": i,
                "url": url,
                "token": os.environ.get(f"LEM_CONNECTION_{i}_TOKEN", ""),
                "name": os.environ.get(f"LEM_CONNECTION_{i}_NAME", f"LEM {i}"),
            }
        )
    return connections


def next_lem_connection_index() -> int:
    """Return the lowest unused LEM connection index."""
    for i in range(1, 50):
        if not os.environ.get(f"LEM_CONNECTION_{i}_URL", ""):
            return i
    return 50


def remove_lem_connection(index: int):
    """Delete all keys for a LEM connection index."""
    for suffix in ("URL", "TOKEN", "NAME"):
        mcp_env_remover(f"LEM_CONNECTION_{index}_{suffix}")


def activate_lem_connection(index: int):
    """Copy LEM connection credentials to EDGE_MANAGER_URL / EDGE_API_TOKEN.
    Clears bridge-only project/device ids, since standalone LEM does not target
    a specific edge.
    """
    url = os.environ.get(f"LEM_CONNECTION_{index}_URL", "")
    token = os.environ.get(f"LEM_CONNECTION_{index}_TOKEN", "")
    mcp_env_updater("EDGE_MANAGER_URL", url)
    mcp_env_updater("EDGE_API_TOKEN", token)
    mcp_env_updater("EDGE_MANAGER_PROJECT_ID", "")
    mcp_env_updater("EDGE_MANAGER_DEVICE_ID", "")
    mcp_env_updater(ACTIVE_LEM_CONNECTION, str(index))
    mcp_env_loader()


def migrate_legacy_lem_settings():
    """One-time import: if EDGE_MANAGER_URL / EDGE_API_TOKEN are set but no
    LEM_CONNECTION_{i} entries exist, create LEM_CONNECTION_1 from them and
    mark it active. Idempotent: a no-op if any LEM_CONNECTION_{i} already
    exists, regardless of whether EDGE_MANAGER_URL is set.
    """
    if get_lem_connections():
        return
    legacy_url = os.environ.get("EDGE_MANAGER_URL", "").strip()
    legacy_token = os.environ.get("EDGE_API_TOKEN", "").strip()
    if not legacy_url or not legacy_token:
        return
    # Heuristic name: hostname of the URL, or "LEM 1" as fallback.
    try:
        from urllib.parse import urlparse

        host = urlparse(
            legacy_url if "://" in legacy_url else f"https://{legacy_url}"
        ).hostname
        name = host or "LEM 1"
    except Exception:
        name = "LEM 1"
    mcp_env_updater("LEM_CONNECTION_1_URL", legacy_url)
    mcp_env_updater("LEM_CONNECTION_1_TOKEN", legacy_token)
    mcp_env_updater("LEM_CONNECTION_1_NAME", name)
    # Only auto-activate if no bridge-mode edge instance is active. A bridge
    # instance sets EDGE_MANAGER_PROJECT_ID; if that is set, the LEM creds
    # belong to that instance, not a standalone connection.
    if not os.environ.get("EDGE_MANAGER_PROJECT_ID", ""):
        mcp_env_updater(ACTIVE_LEM_CONNECTION, "1")


# ── Model / API key selection ───────────────────────────────────────────────


def check_model_key() -> Tuple[bool, str]:
    anthropic_exists = os.environ.get(key_of_anthropic_api_key)
    openai_exists = os.environ.get(key_of_openai_api_key)
    gemini_exists = os.environ.get(key_of_gemini_api_key)
    preferred_model = os.environ.get(MODEL_PREFERENCE)

    if preferred_model is not None and preferred_model in [
        MODEL_NAME_OPENAI,
        MODEL_NAME_ANTHROPIC,
        MODEL_NAME_GEMINI,
    ]:
        return True, preferred_model

    if anthropic_exists is None and openai_exists is None and gemini_exists is None:
        return False, ""

    if anthropic_exists:
        mcp_env_updater(MODEL_PREFERENCE, MODEL_NAME_ANTHROPIC)
        return True, MODEL_NAME_ANTHROPIC

    if openai_exists:
        mcp_env_updater(MODEL_PREFERENCE, MODEL_NAME_OPENAI)
        return True, MODEL_NAME_OPENAI

    mcp_env_updater(MODEL_PREFERENCE, MODEL_NAME_GEMINI)
    return True, MODEL_NAME_GEMINI
