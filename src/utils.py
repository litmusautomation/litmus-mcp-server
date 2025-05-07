import ssl
import os
import logging
from typing import List, Dict, Any, Tuple
import dotenv

NATS_SOURCE = "10.30.50.1"
NATS_PORT = "4222"
MCP_PORT = 8000

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JINJA_TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")


EDGE_URL = ""
EDGE_API_CLIENT_ID = ""
EDGE_API_CLIENT_SECRET = ""
VALIDATE_CERTIFICATE = ""
ANTHROPIC_KEY = ""

_key_EDGE_URL = "EDGE_URL"
_key_EDGE_API_CLIENT_ID = "EDGE_API_CLIENT_ID"
_key_EDGE_API_CLIENT_SECRET = "EDGE_API_CLIENT_SECRET"
_key_VALIDATE_CERTIFICATE = "VALIDATE_CERTIFICATE"
_default_value_VALIDATE_CERTIFICATE = "false"

key_of_anthropic_api_key = "ANTHROPIC_API_KEY"
key_of_openai_api_key = "OPENAI_API_KEY"
MODEL_NAME_OPENAI = "openai"
MODEL_NAME_ANTHROPIC = "anthropic"
MODEL_PREFERENCE = "PREFERRED_MODEL"

# Maximum number of message pairs to keep in history
MAX_HISTORY_PAIRS = 5

# Store conversation history in memory (this will be reset if the server restarts)
CONVERSATION_HISTORY = []
STREAMING_ALLOWED = True


def ssl_config():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    return ssl_ctx


def mcp_env_loader():
    global EDGE_URL, EDGE_API_CLIENT_ID, EDGE_API_CLIENT_SECRET, VALIDATE_CERTIFICATE, ANTHROPIC_KEY

    # Load .env file if available
    dotenv_path = dotenv.find_dotenv()
    if dotenv_path:
        dotenv.load_dotenv(dotenv_path, override=True)
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
        logging.info(
            "Did not find .env file in current working directory. Defaulting to system variables"
        )

    EDGE_URL = os.environ.get(_key_EDGE_URL, "")
    EDGE_API_CLIENT_ID = os.environ.get(_key_EDGE_API_CLIENT_ID, "")
    EDGE_API_CLIENT_SECRET = os.environ.get(_key_EDGE_API_CLIENT_SECRET, "")
    VALIDATE_CERTIFICATE = os.environ.get(
        _key_VALIDATE_CERTIFICATE, _default_value_VALIDATE_CERTIFICATE
    )
    ANTHROPIC_KEY = os.environ.get(key_of_anthropic_api_key, "")


def _get_env_vars(env_file, override):
    path_to_env = dotenv.find_dotenv(env_file)
    if path_to_env == "" or None:
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

    # Write updated content back to .env
    with open(path_to_env, "w") as file:
        for k, v in env_vars.items():
            file.write(f"{k}={v}\n")

    print(f"Updated {key} in {env_file}")


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


def get_conversation_history() -> List[Dict[str, Any]]:
    """Get global conversation history"""
    global CONVERSATION_HISTORY
    return CONVERSATION_HISTORY


def update_conversation_history(query: str | None, response: str | None, clear=False):
    """Update the global conversation history"""
    global CONVERSATION_HISTORY
    if clear:
        CONVERSATION_HISTORY = []
        return

    # Add new messages
    CONVERSATION_HISTORY.append({"role": "user", "content": query})
    CONVERSATION_HISTORY.append({"role": "assistant", "content": response})

    # Trim to keep only MAX_HISTORY_PAIRS
    if len(CONVERSATION_HISTORY) > MAX_HISTORY_PAIRS * 2:
        CONVERSATION_HISTORY = CONVERSATION_HISTORY[-MAX_HISTORY_PAIRS * 2 :]


def get_chat_log(conversation_history) -> List[Dict[str, Any]]:
    """Get chat log"""
    chat_log = []

    # Format conversation history for display
    for i in range(0, len(conversation_history), 2):
        if i + 1 < len(conversation_history):
            chat_log.append(
                {
                    "user": conversation_history[i]["content"],
                    "assistant": conversation_history[i + 1]["content"],
                }
            )
    return chat_log


def check_streaming_status(current_route: str) -> Tuple[bool, str]:
    redirect = False
    new_route = current_route

    if STREAMING_ALLOWED and current_route == "/":
        redirect = True
        new_route = "/streaming"
        return redirect, new_route
    if not STREAMING_ALLOWED and current_route == "/streaming":
        redirect = True
        new_route = "/"
        return redirect, new_route

    return redirect, new_route


def markdown_to_html(text):
    if not text:
        return ""
    return text.replace("\n", "<br>")


if __name__ == "__main__":
    # env_loader()
    print(BASE_DIR)
    print(JINJA_TEMPLATE_DIR)
