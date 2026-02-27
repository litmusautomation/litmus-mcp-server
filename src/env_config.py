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

NATS_SOURCE = "10.30.50.1"
NATS_PORT = "4222"
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
MODEL_NAME_OPENAI = "openai"
MODEL_NAME_ANTHROPIC = "anthropic"
MODEL_PREFERENCE = "PREFERRED_MODEL"
PREFERRED_MODEL_ID = "PREFERRED_MODEL_ID"
ACTIVE_EDGE_INSTANCE = "ACTIVE_EDGE_INSTANCE"


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
    """Delete all 4 keys for an instance index."""
    for suffix in ("URL", "CLIENT_ID", "SECRET", "NAME"):
        mcp_env_remover(f"EDGE_INSTANCE_{index}_{suffix}")


def activate_edge_instance(index: int):
    """Copy instance credentials to main EDGE_ vars and write ACTIVE_EDGE_INSTANCE."""
    url = os.environ.get(f"EDGE_INSTANCE_{index}_URL", "")
    cid = os.environ.get(f"EDGE_INSTANCE_{index}_CLIENT_ID", "")
    sec = os.environ.get(f"EDGE_INSTANCE_{index}_SECRET", "")
    mcp_env_updater("EDGE_URL", url)
    mcp_env_updater("EDGE_API_CLIENT_ID", cid)
    mcp_env_updater("EDGE_API_CLIENT_SECRET", sec)
    mcp_env_updater(ACTIVE_EDGE_INSTANCE, str(index))
    mcp_env_loader()


# ── Model / API key selection ───────────────────────────────────────────────

def check_model_key() -> Tuple[bool, str]:
    anthropic_exists = os.environ.get(key_of_anthropic_api_key)
    openai_exists = os.environ.get(key_of_openai_api_key)
    preferred_model = os.environ.get(MODEL_PREFERENCE)

    if preferred_model is not None and preferred_model in [
        MODEL_NAME_OPENAI,
        MODEL_NAME_ANTHROPIC,
    ]:
        return True, preferred_model

    if anthropic_exists is None and openai_exists is None:
        return False, ""

    if openai_exists:
        mcp_env_updater(MODEL_PREFERENCE, MODEL_NAME_OPENAI)
        return True, MODEL_NAME_OPENAI

    mcp_env_updater(MODEL_PREFERENCE, MODEL_NAME_ANTHROPIC)
    return True, MODEL_NAME_ANTHROPIC
