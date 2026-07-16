import os
import secrets
import asyncio
import logging
import warnings
import urllib3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
    JSONResponse,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import HTTP_303_SEE_OTHER
import uvicorn
from anthropic import AsyncAnthropic
import subprocess
import sys

from env_config import (
    mcp_env_loader,
    mcp_env_updater,
    mcp_env_remover,
    key_of_anthropic_api_key,
    key_of_openai_api_key,
    key_of_gemini_api_key,
    check_model_key,
    MODEL_NAME_ANTHROPIC,
    MODEL_NAME_OPENAI,
    MODEL_NAME_GEMINI,
    MODEL_PREFERENCE,
    PREFERRED_MODEL_ID,
    ACTIVE_EDGE_INSTANCE,
    ACTIVE_LEM_CONNECTION,
    CLIENT_SESSION_TIMEOUT_SECONDS,
    CLIENT_SESSION_TIMEOUT_SECONDS_MIN,
    CLIENT_SESSION_TIMEOUT_SECONDS_MAX,
    SAVE_SETTINGS_ALLOWED_KEYS,
    get_edge_instances,
    next_edge_instance_index,
    remove_edge_instance,
    activate_edge_instance,
    get_lem_connections,
    next_lem_connection_index,
    remove_lem_connection,
    activate_lem_connection,
    JINJA_TEMPLATE_DIR,
    STATIC_DIR,
)
from conversation import (
    get_conversation_history,
    update_conversation_history,
    get_chat_log,
    markdown_to_html,
    clear_all_sessions,
)
from client_utils import MCPClient
from tools.resource_tools import DOCUMENTATION_RESOURCES
from server import ALL_TOOLS

warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("web_client").setLevel(logging.INFO)
logger = logging.getLogger("web_client")

mcp_env_loader()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("MCP client initialised (per-query connections)")
    app.state.client = MCPClient()
    yield
    logger.info("MCP client shut down")


def _parse_cors_origins(value: str) -> list[str]:
    """Comma-separated explicit origins; empty means same-origin only."""
    return [o.strip() for o in (value or "").split(",") if o.strip()]


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=JINJA_TEMPLATE_DIR)
templates.env.filters["markdown_to_html"] = markdown_to_html
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# The UI is served same-origin by this app, so no CORS is needed by default.
# Cross-origin access (e.g. a separately hosted frontend) must be opted into
# with an explicit origin list; wildcard origins are not allowed because the
# UI relies on a session cookie.
_cors_origins = _parse_cors_origins(os.environ.get("WEB_UI_CORS_ORIGINS", ""))
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ── Session helpers ─────────────────────────────────────────────────────────

_SESSION_COOKIE = "litmus_sid"


def _get_session_id(request: Request) -> str:
    """Return the existing session ID from cookie, or generate a new one."""
    return request.cookies.get(_SESSION_COOKIE) or secrets.token_urlsafe(16)


# ── Helpers ────────────────────────────────────────────────────────────────


def _get_client(request: Request) -> MCPClient:
    client = getattr(request.app.state, "client", None)
    if not client:
        raise HTTPException(status_code=500, detail="MCP client not initialised")
    return client


# ── Auth / setup ───────────────────────────────────────────────────────────


@app.get("/api/models", name="api_models")
async def api_models(provider: str):
    mcp_env_loader()
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return JSONResponse(
                {"error": "No Anthropic key configured"}, status_code=400
            )
        try:
            client = AsyncAnthropic(api_key=key)
            page = await client.models.list(limit=100)
            return JSONResponse(
                {"models": [{"id": m.id, "name": m.display_name} for m in page.data]}
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
    elif provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return JSONResponse({"error": "No OpenAI key configured"}, status_code=400)
        try:
            from openai import AsyncOpenAI

            oai = AsyncOpenAI(api_key=key)
            page = await oai.models.list()
            chat_prefixes = ("gpt-", "o1-", "o3-", "o4-")
            models = sorted(
                [
                    m
                    for m in page.data
                    if any(m.id.startswith(p) for p in chat_prefixes)
                ],
                key=lambda m: m.id,
                reverse=True,
            )
            return JSONResponse(
                {"models": [{"id": m.id, "name": m.id} for m in models]}
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
    elif provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            return JSONResponse({"error": "No Gemini key configured"}, status_code=400)
        try:
            from google import genai

            def _list_gemini_models():
                client = genai.Client(api_key=key)
                return list(client.models.list())

            model_list = await asyncio.to_thread(_list_gemini_models)
            models = [
                {
                    "id": (
                        m.name.replace("models/", "")
                        if m.name.startswith("models/")
                        else m.name
                    ),
                    "name": m.display_name or m.name,
                }
                for m in model_list
                if "gemini" in (m.name or "").lower()
            ]
            return JSONResponse({"models": models})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"error": "Unknown provider"}, status_code=400)


@app.api_route(
    "/setup", methods=["GET", "POST"], response_class=HTMLResponse, name="setup"
)
async def setup(request: Request):
    if request.method == "GET":
        return templates.TemplateResponse(
            request, "setup.html", {"active_page": "setup"}
        )

    form = await request.form()
    anthropic_key = form.get("value_of_anthropic_api_key", "").strip()
    openai_key = form.get("value_of_openai_api_key", "").strip()
    gemini_key = form.get("value_of_gemini_api_key", "").strip()

    if not anthropic_key and not openai_key and not gemini_key:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"error": "Please provide at least one API key."},
        )

    if anthropic_key:
        mcp_env_updater(key_of_anthropic_api_key, anthropic_key)
    if openai_key:
        mcp_env_updater(key_of_openai_api_key, openai_key)
    if gemini_key:
        mcp_env_updater(key_of_gemini_api_key, gemini_key)

    return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)


@app.post("/setup-key", name="setup_key")
async def setup_key(request: Request):
    form = await request.form()
    anthropic_key = form.get("value_of_anthropic_api_key", "").strip()
    openai_key = form.get("value_of_openai_api_key", "").strip()
    gemini_key = form.get("value_of_gemini_api_key", "").strip()
    saved = []
    if anthropic_key:
        mcp_env_updater(key_of_anthropic_api_key, anthropic_key)
        mcp_env_loader()
        saved.append("anthropic")
    if openai_key:
        mcp_env_updater(key_of_openai_api_key, openai_key)
        mcp_env_loader()
        saved.append("openai")
    if gemini_key:
        mcp_env_updater(key_of_gemini_api_key, gemini_key)
        mcp_env_loader()
        saved.append("gemini")
    if not saved:
        return JSONResponse({"error": "Provide at least one key"}, status_code=400)
    return JSONResponse({"saved": saved})


@app.post("/switch-model", response_class=HTMLResponse, name="switch_model")
async def switch_model(
    request: Request,
    switch_model_to: str = Form(...),
    model_id: str = Form(default=""),
):
    if switch_model_to.startswith("anthropic"):
        preference = MODEL_NAME_ANTHROPIC
    elif switch_model_to.startswith("gemini"):
        preference = MODEL_NAME_GEMINI
    else:
        preference = MODEL_NAME_OPENAI
    mcp_env_updater(MODEL_PREFERENCE, preference)
    if model_id:
        mcp_env_updater(PREFERRED_MODEL_ID, model_id)
    mcp_env_loader()
    return RedirectResponse("/update-env?updated=true", status_code=HTTP_303_SEE_OTHER)


@app.post("/api/save-settings", name="api_save_settings")
async def api_save_settings(request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        return JSONResponse(
            {"ok": False, "error": "expected a JSON object"}, status_code=400
        )

    unknown = set(data.keys()) - SAVE_SETTINGS_ALLOWED_KEYS
    if unknown:
        return JSONResponse(
            {"ok": False, "error": f"unknown setting(s): {sorted(unknown)}"},
            status_code=400,
        )

    if CLIENT_SESSION_TIMEOUT_SECONDS in data:
        raw = data[CLIENT_SESSION_TIMEOUT_SECONDS]
        try:
            seconds = int(raw)
        except (TypeError, ValueError):
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"{CLIENT_SESSION_TIMEOUT_SECONDS} must be an integer",
                },
                status_code=400,
            )
        if not (
            CLIENT_SESSION_TIMEOUT_SECONDS_MIN
            <= seconds
            <= CLIENT_SESSION_TIMEOUT_SECONDS_MAX
        ):
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        f"{CLIENT_SESSION_TIMEOUT_SECONDS} must be between "
                        f"{CLIENT_SESSION_TIMEOUT_SECONDS_MIN} and "
                        f"{CLIENT_SESSION_TIMEOUT_SECONDS_MAX}"
                    ),
                },
                status_code=400,
            )
        data[CLIENT_SESSION_TIMEOUT_SECONDS] = str(seconds)

    for key, value in data.items():
        if value:  # only write non-empty values
            mcp_env_updater(key, value)
    mcp_env_loader()
    return JSONResponse({"ok": True})


@app.get("/update-env", response_class=HTMLResponse, name="update_env_form")
async def update_env_form(request: Request):
    mcp_env_loader()
    _, model_type = check_model_key()
    current_model_id = os.environ.get(PREFERRED_MODEL_ID, "")
    edge_instances = get_edge_instances()
    active_edge_instance = int(os.environ.get(ACTIVE_EDGE_INSTANCE, 0))

    _host = request.headers.get("host", "localhost:9000").split(":")[0]
    _default_sse = f"http://{_host}:8000/sse"
    _env_sse = os.environ.get("MCP_SSE_URL", "")
    mcp_sse_url = (
        _env_sse
        if (_env_sse and _env_sse != "http://localhost:8000/sse")
        else _default_sse
    )

    mcp_config = {
        "mcp_sse_url": mcp_sse_url,
        "edge_url": os.environ.get("EDGE_URL", ""),
        "client_id": os.environ.get("EDGE_API_CLIENT_ID", ""),
        "client_secret": os.environ.get("EDGE_API_CLIENT_SECRET", ""),
        "edge_manager_url": os.environ.get("EDGE_MANAGER_URL", ""),
        "edge_api_token": os.environ.get("EDGE_API_TOKEN", ""),
        "edge_manager_project_id": os.environ.get("EDGE_MANAGER_PROJECT_ID", ""),
        "edge_manager_device_id": os.environ.get("EDGE_MANAGER_DEVICE_ID", ""),
        "nats_source": os.environ.get("NATS_SOURCE", ""),
        "nats_port": os.environ.get("NATS_PORT", "4222"),
        "nats_password": os.environ.get("NATS_PASSWORD", ""),
        "nats_tls": os.environ.get("NATS_TLS", "true"),
        "influx_host": os.environ.get("INFLUX_HOST", ""),
        "influx_port": os.environ.get("INFLUX_PORT", "8086"),
        "influx_db_name": os.environ.get("INFLUX_DB_NAME", "tsdata"),
        "influx_username": os.environ.get("INFLUX_USERNAME", ""),
        "influx_password": os.environ.get("INFLUX_PASSWORD", ""),
    }
    settings = {
        "anthropic_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "openai_key": os.environ.get("OPENAI_API_KEY", ""),
        "gemini_key": os.environ.get("GEMINI_API_KEY", ""),
        "validate_cert": os.environ.get("VALIDATE_CERTIFICATE", "false"),
    }

    current_client_timeout = (
        os.environ.get("CLIENT_SESSION_TIMEOUT_SECONDS", "60") or "60"
    )

    return templates.TemplateResponse(
        request,
        "update_env.html",
        {
            "current_model": model_type,
            "current_model_id": current_model_id,
            "current_client_timeout": current_client_timeout,
            "edge_instances": edge_instances,
            "active_edge_instance": active_edge_instance,
            "mcp_config": mcp_config,
            "settings": settings,
            "active_page": "config",
        },
    )


@app.post("/update-env", response_class=HTMLResponse, name="update_env_submit")
async def update_env_submit(
    request: Request, env_key: str = Form(...), env_value: str = Form(...)
):
    mcp_env_updater(env_key, env_value)
    mcp_env_loader()
    return RedirectResponse("/update-env?updated=true", status_code=HTTP_303_SEE_OTHER)


@app.post("/remove-env", response_class=HTMLResponse, name="remove_env")
async def remove_env_submit(request: Request, env_key: str = Form(...)):
    mcp_env_remover(key=env_key)
    mcp_env_loader()
    return RedirectResponse("/update-env?updated=true", status_code=HTTP_303_SEE_OTHER)


@app.post("/clear-history", response_class=HTMLResponse, name="clear_history")
async def clear_history(request: Request):
    session_id = _get_session_id(request)
    update_conversation_history(session_id, None, None, clear=True)
    return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)


@app.post("/api/switch-model", name="api_switch_model")
async def api_switch_model(
    request: Request, provider: str = Form(...), model_id: str = Form(...)
):
    if provider == "anthropic":
        preference = MODEL_NAME_ANTHROPIC
    elif provider == "gemini":
        preference = MODEL_NAME_GEMINI
    else:
        preference = MODEL_NAME_OPENAI
    mcp_env_updater(MODEL_PREFERENCE, preference)
    mcp_env_updater(PREFERRED_MODEL_ID, model_id)
    mcp_env_loader()
    return JSONResponse({"ok": True, "provider": provider, "model_id": model_id})


# ── Edge instance management ────────────────────────────────────────────────


@app.get("/api/edge-instances", name="api_edge_instances")
async def api_edge_instances():
    mcp_env_loader()
    instances = get_edge_instances()
    active = int(os.environ.get(ACTIVE_EDGE_INSTANCE, 0))
    return JSONResponse({"instances": instances, "active": active})


@app.post("/api/add-edge-instance", name="api_add_edge_instance")
async def api_add_edge_instance(
    request: Request,
    url: str = Form(default=""),
    client_id: str = Form(default=""),
    client_secret: str = Form(default=""),
    manager_url: str = Form(default=""),
    api_token: str = Form(default=""),
    project_id: str = Form(default=""),
    device_id: str = Form(default=""),
):
    mcp_env_loader()
    validate_cert = os.environ.get("VALIDATE_CERTIFICATE", "false").lower() == "true"
    is_lem = bool(manager_url.strip())

    if is_lem:
        manager_url = manager_url.strip().rstrip("/")
        api_token = api_token.strip()
        project_id = project_id.strip()
        device_id = device_id.strip()
        if not api_token or not project_id or not device_id:
            return JSONResponse(
                {"error": "LEM bridge requires API Token, Project ID, and Device ID"},
                status_code=400,
            )
        bridge_base = f"{manager_url}/api/v1/edge/{project_id}/{device_id}"

        def _fetch_name_lem():
            import json as _json
            from litmussdk.utils.conn import new_lem_bridge_connection
            from litmussdk.utils.api import direct_request

            conn = new_lem_bridge_connection(
                edge_manager_url=manager_url,
                edge_api_token=api_token,
                project_id=project_id,
                device_id=device_id,
                validate_certificate=validate_cert,
                timeout_seconds=10,
            )
            code, raw = direct_request(
                connection=conn, url=f"{bridge_base}/dm/host/info", request_type="GET"
            )
            try:
                data = _json.loads(raw) if raw else {}
            except Exception:
                data = {}
            if code == 200 and isinstance(data, dict):
                return (
                    data.get("description")
                    or data.get("hostname")
                    or data.get("deviceName")
                    or data.get("name")
                    or ""
                )
            return ""

        try:
            name = await asyncio.to_thread(_fetch_name_lem)
        except Exception as exc:
            logger.exception(f"add-edge-instance (LEM): connection failed: {exc}")
            return JSONResponse(
                {"error": f"Could not connect via LEM bridge: {exc}"}, status_code=400
            )

        idx = next_edge_instance_index()
        if not name:
            name = f"LEM Bridge {idx}"
        mcp_env_updater(f"EDGE_INSTANCE_{idx}_URL", manager_url)
        mcp_env_updater(f"EDGE_INSTANCE_{idx}_TYPE", "lem")
        mcp_env_updater(f"EDGE_INSTANCE_{idx}_API_TOKEN", api_token)
        mcp_env_updater(f"EDGE_INSTANCE_{idx}_PROJECT_ID", project_id)
        mcp_env_updater(f"EDGE_INSTANCE_{idx}_DEVICE_ID", device_id)
        mcp_env_updater(f"EDGE_INSTANCE_{idx}_NAME", name)
        mcp_env_loader()

        active = int(os.environ.get(ACTIVE_EDGE_INSTANCE, 0))
        if not active:
            activate_edge_instance(idx)
            mcp_env_loader()

        return JSONResponse(
            {"ok": True, "index": idx, "name": name, "url": manager_url, "type": "lem"}
        )

    # Direct connection path
    if not url or not client_id or not client_secret:
        return JSONResponse(
            {"error": "Direct connection requires URL, Client ID, and Client Secret"},
            status_code=400,
        )
    url = url.strip().rstrip("/")

    def _fetch_name():
        import json as _json
        from litmussdk.utils.conn import new_le_connection
        from litmussdk.utils.api import direct_request

        conn = new_le_connection(
            edge_url=url,
            client_id=client_id,
            client_secret=client_secret,
            validate_certificate=validate_cert,
            timeout_seconds=10,
        )
        code, raw = direct_request(
            connection=conn, url=f"{url}/dm/host/info", request_type="GET"
        )
        try:
            data = _json.loads(raw) if raw else {}
        except Exception:
            data = {}
        if code == 200 and isinstance(data, dict):
            return (
                data.get("description")
                or data.get("hostname")
                or data.get("deviceName")
                or data.get("name")
                or ""
            )
        return ""

    try:
        name = await asyncio.to_thread(_fetch_name)
    except Exception as exc:
        logger.exception(f"add-edge-instance: connection failed: {exc}")
        return JSONResponse({"error": f"Could not connect: {exc}"}, status_code=400)

    idx = next_edge_instance_index()
    if not name:
        name = f"Edge {idx}"
    mcp_env_updater(f"EDGE_INSTANCE_{idx}_URL", url)
    mcp_env_updater(f"EDGE_INSTANCE_{idx}_CLIENT_ID", client_id)
    mcp_env_updater(f"EDGE_INSTANCE_{idx}_SECRET", client_secret)
    mcp_env_updater(f"EDGE_INSTANCE_{idx}_TYPE", "direct")
    mcp_env_updater(f"EDGE_INSTANCE_{idx}_NAME", name)
    mcp_env_loader()

    active = int(os.environ.get(ACTIVE_EDGE_INSTANCE, 0))
    if not active:
        activate_edge_instance(idx)
        mcp_env_loader()

    return JSONResponse(
        {"ok": True, "index": idx, "name": name, "url": url, "type": "direct"}
    )


@app.post("/api/remove-edge-instance", name="api_remove_edge_instance")
async def api_remove_edge_instance(request: Request, index: int = Form(...)):
    mcp_env_loader()
    active = int(os.environ.get(ACTIVE_EDGE_INSTANCE, 0))
    remove_edge_instance(index)
    mcp_env_loader()
    if active == index:
        instances = get_edge_instances()
        if instances:
            activate_edge_instance(instances[0]["index"])
            mcp_env_loader()
        else:
            mcp_env_updater(ACTIVE_EDGE_INSTANCE, "0")
    return JSONResponse({"ok": True})


@app.post("/api/switch-edge-instance", name="api_switch_edge_instance")
async def api_switch_edge_instance(request: Request, index: int = Form(...)):
    mcp_env_loader()
    activate_edge_instance(index)
    mcp_env_loader()
    clear_all_sessions()
    name = os.environ.get(f"EDGE_INSTANCE_{index}_NAME", f"Edge {index}")
    return JSONResponse({"ok": True, "index": index, "name": name})


# ── LEM standalone connections ─────────────────────────────────────────────


def _default_lem_admin_url(manager_url: str) -> str:
    """Replace the URL host's port with 8446. Mirrors utils.auth._default_admin_url."""
    from urllib.parse import urlparse

    raw = manager_url if "://" in manager_url else f"https://{manager_url}"
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or manager_url.split("/")[0].split(":")[0]
    return f"{scheme}://{host}:8446"


def _build_lem_connection(
    manager_url: str, api_token: str, validate_certificate: bool, timeout: int = 10
):
    from litmussdk.utils.conn import new_lem_connection

    return new_lem_connection(
        edge_manager_url=manager_url,
        edge_manager_admin_url=_default_lem_admin_url(manager_url),
        edge_api_token=api_token,
        validate_certificate=validate_certificate,
        timeout_seconds=timeout,
    )


@app.get("/api/lem-connections", name="api_lem_connections")
async def api_lem_connections():
    mcp_env_loader()
    connections = get_lem_connections()
    # Strip tokens before returning to the browser; never echo secrets.
    safe = [{k: v for k, v in c.items() if k != "token"} for c in connections]
    active = int(os.environ.get(ACTIVE_LEM_CONNECTION, 0))
    return JSONResponse({"connections": safe, "active": active})


@app.post("/api/add-lem-connection", name="api_add_lem_connection")
async def api_add_lem_connection(
    request: Request,
    manager_url: str = Form(default=""),
    api_token: str = Form(default=""),
    name: str = Form(default=""),
):
    mcp_env_loader()
    validate_cert = os.environ.get("VALIDATE_CERTIFICATE", "false").lower() == "true"
    manager_url = manager_url.strip().rstrip("/")
    api_token = api_token.strip()
    name = name.strip()
    if not manager_url or not api_token:
        return JSONResponse(
            {"error": "Manager URL and API token are required."}, status_code=400
        )

    def _verify():
        from litmussdk.lem.lifecycle.dashboard import deployment_info as _deploy

        conn = _build_lem_connection(manager_url, api_token, validate_cert)
        return _deploy(raw=True, connection=conn)

    try:
        deployment = await asyncio.to_thread(_verify)
    except Exception as exc:
        logger.exception(f"add-lem-connection: connection failed: {exc}")
        return JSONResponse(
            {"error": f"Could not reach LEM: {exc}"}, status_code=400
        )

    idx = next_lem_connection_index()
    if not name:
        from urllib.parse import urlparse

        host = urlparse(manager_url).hostname or f"LEM {idx}"
        name = host
    mcp_env_updater(f"LEM_CONNECTION_{idx}_URL", manager_url)
    mcp_env_updater(f"LEM_CONNECTION_{idx}_TOKEN", api_token)
    mcp_env_updater(f"LEM_CONNECTION_{idx}_NAME", name)
    mcp_env_loader()

    active = int(os.environ.get(ACTIVE_LEM_CONNECTION, 0))
    if not active:
        activate_lem_connection(idx)
        mcp_env_loader()

    return JSONResponse(
        {
            "ok": True,
            "index": idx,
            "name": name,
            "url": manager_url,
            "deployment": deployment,
        }
    )


@app.post("/api/remove-lem-connection", name="api_remove_lem_connection")
async def api_remove_lem_connection(request: Request, index: int = Form(...)):
    mcp_env_loader()
    active = int(os.environ.get(ACTIVE_LEM_CONNECTION, 0))
    remove_lem_connection(index)
    mcp_env_loader()
    if active == index:
        connections = get_lem_connections()
        if connections:
            activate_lem_connection(connections[0]["index"])
            mcp_env_loader()
        else:
            mcp_env_updater(ACTIVE_LEM_CONNECTION, "0")
            # Also clear EDGE_MANAGER_URL/TOKEN: the connection that owned them
            # is gone and no other connection exists to take over.
            mcp_env_updater("EDGE_MANAGER_URL", "")
            mcp_env_updater("EDGE_API_TOKEN", "")
            mcp_env_loader()
    return JSONResponse({"ok": True})


@app.post("/api/switch-lem-connection", name="api_switch_lem_connection")
async def api_switch_lem_connection(request: Request, index: int = Form(...)):
    mcp_env_loader()
    if int(index) == 0:
        # Explicit "none": clear active LEM and the mirrored env vars.
        mcp_env_updater(ACTIVE_LEM_CONNECTION, "0")
        mcp_env_updater("EDGE_MANAGER_URL", "")
        mcp_env_updater("EDGE_API_TOKEN", "")
        mcp_env_updater("EDGE_MANAGER_PROJECT_ID", "")
        mcp_env_updater("EDGE_MANAGER_DEVICE_ID", "")
        mcp_env_loader()
        clear_all_sessions()
        return JSONResponse({"ok": True, "index": 0, "name": ""})
    activate_lem_connection(index)
    mcp_env_loader()
    clear_all_sessions()
    name = os.environ.get(f"LEM_CONNECTION_{index}_NAME", f"LEM {index}")
    return JSONResponse({"ok": True, "index": index, "name": name})


@app.get("/api/lem/test", name="api_lem_test")
async def api_lem_test():
    """Probe the currently active LEM credentials and return summary info for the config page."""
    mcp_env_loader()
    manager_url = os.environ.get("EDGE_MANAGER_URL", "").rstrip("/")
    api_token = os.environ.get("EDGE_API_TOKEN", "")
    if not manager_url or not api_token:
        return JSONResponse({"status": "not_configured"})
    validate_cert = os.environ.get("VALIDATE_CERTIFICATE", "false").lower() == "true"

    def _probe():
        from litmussdk.lem.lifecycle.dashboard import (
            deployment_info as _deploy,
            get_system_time as _time,
        )
        from litmussdk.lem.companies import list_all_company_stats as _stats

        conn = _build_lem_connection(manager_url, api_token, validate_cert)
        out = {"status": "ok"}
        try:
            out["deployment"] = _deploy(raw=True, connection=conn)
        except Exception as e:
            out["deployment_error"] = str(e)
        try:
            out["system_time"] = _time(raw=True, connection=conn)
        except Exception as e:
            out["system_time_error"] = str(e)
        try:
            companies = _stats(raw=True, connection=conn) or []
            # Trim to a small summary (top by num_devices).
            try:
                companies = sorted(
                    companies,
                    key=lambda c: c.get("totalNumOfDevices", 0)
                    if isinstance(c, dict)
                    else 0,
                    reverse=True,
                )
            except Exception:
                pass
            out["company_count"] = len(companies)
            out["companies_top"] = companies[:5]
        except Exception as e:
            out["companies_error"] = str(e)
        return out

    try:
        result = await asyncio.to_thread(_probe)
        return JSONResponse(result)
    except Exception as exc:
        logger.exception(f"lem/test failed: {exc}")
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


# ── Main chat ──────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse, name="query_handler")
async def chat_get(request: Request):
    mcp_env_loader()
    client = _get_client(request)
    key_exists, model_type = check_model_key()
    if not key_exists:
        return RedirectResponse("/setup", status_code=HTTP_303_SEE_OTHER)

    session_id = _get_session_id(request)
    conversation_history = get_conversation_history(session_id)
    chat_log = get_chat_log(conversation_history)
    edge_instances = get_edge_instances()
    active_edge_instance = int(os.environ.get(ACTIVE_EDGE_INSTANCE, 0))
    active_instance_name = (
        os.environ.get(f"EDGE_INSTANCE_{active_edge_instance}_NAME", "")
        if active_edge_instance > 0
        else ""
    )
    lem_connections = [
        {k: v for k, v in c.items() if k != "token"} for c in get_lem_connections()
    ]
    active_lem_connection = int(os.environ.get(ACTIVE_LEM_CONNECTION, 0))
    active_lem_name = (
        os.environ.get(f"LEM_CONNECTION_{active_lem_connection}_NAME", "")
        if active_lem_connection > 0
        else ""
    )

    response = templates.TemplateResponse(
        request,
        "query.html",
        {
            "chat_log": [
                {
                    "user": e.get("user", ""),
                    "assistant": e.get("assistant", ""),
                    "model": e.get("model", client.model_used or model_type),
                }
                for e in chat_log
            ],
            "has_history": bool(chat_log),
            "model": model_type,
            "current_model_id": os.environ.get(PREFERRED_MODEL_ID, ""),
            "edge_instances": edge_instances,
            "active_edge_instance": active_edge_instance,
            "active_instance_name": active_instance_name,
            "lem_connections": lem_connections,
            "active_lem_connection": active_lem_connection,
            "active_lem_name": active_lem_name,
            "active_page": "home",
        },
    )
    if not request.cookies.get(_SESSION_COOKIE):
        response.set_cookie(_SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    return response


@app.post("/chat", name="chat_post")
async def chat_post(request: Request):
    """
    Streaming endpoint for the chat UI.

    For Anthropic: streams tokens as plain text, with [Tool: name] markers.
    For OpenAI: returns the full response as a single streamed chunk.
    """
    mcp_env_loader()
    client = _get_client(request)
    key_exists, model_type = check_model_key()
    if not key_exists:
        return JSONResponse(status_code=401, content={"error": "No API key configured"})

    form = await request.form()
    query = form.get("query", "").strip()
    if not query:
        return JSONResponse(status_code=400, content={"error": "Query cannot be empty"})

    session_id = _get_session_id(request)
    conversation_history = get_conversation_history(session_id)

    if model_type == MODEL_NAME_ANTHROPIC:

        async def anthropic_stream():
            full_response = ""
            try:
                async for chunk in client.process_streaming_query(
                    query, conversation_history=conversation_history
                ):
                    full_response += chunk
                    yield chunk
            except Exception as e:
                logger.exception(f"Streaming error: {e}")
                error_msg = f"\n[Error: {e}]"
                full_response += error_msg
                yield error_msg
            finally:
                update_conversation_history(session_id, query, full_response)

        return StreamingResponse(
            anthropic_stream(),
            media_type="text/plain",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    elif model_type == MODEL_NAME_GEMINI:

        async def gemini_stream():
            full_response = ""
            try:
                async for chunk in client.process_streaming_query_gemini(
                    query, conversation_history=conversation_history
                ):
                    full_response += chunk
                    yield chunk
            except Exception as e:
                logger.exception(f"Gemini streaming error: {e}")
                error_msg = f"\n[Error: {e}]"
                full_response += error_msg
                yield error_msg
            finally:
                update_conversation_history(session_id, query, full_response)

        return StreamingResponse(
            gemini_stream(),
            media_type="text/plain",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    else:  # OpenAI — non-streaming, wrap as single-chunk stream

        async def openai_stream():
            full_response = ""
            try:
                result = await client.process_query_with_openai_agent(
                    query, conversation_history=conversation_history
                )
                full_response = result.final_output
                yield full_response
            except Exception as e:
                logger.exception(f"OpenAI error: {e}")
                full_response = f"Error: {e}"
                yield full_response
            finally:
                update_conversation_history(session_id, query, full_response)

        return StreamingResponse(
            openai_stream(),
            media_type="text/plain",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


# ── Legacy /streaming redirect ─────────────────────────────────────────────


@app.api_route("/streaming", methods=["GET", "POST"], name="streaming_query_handler")
async def streaming_redirect(request: Request):
    return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)


# ── MCP info ───────────────────────────────────────────────────────────────


@app.get("/mcp-info", name="mcp_info")
async def mcp_info(request: Request):
    client = _get_client(request)
    try:
        resources = await client._list_resources()
    except Exception:
        resources = []
    return JSONResponse(
        {
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "category": t["category"],
                    "deprecated": bool(t.get("deprecated")),
                }
                for t in ALL_TOOLS
            ],
            "resources": [
                {
                    "uri": r["uri"],
                    "name": r["name"],
                    "description": DOCUMENTATION_RESOURCES.get(r["uri"], {}).get(
                        "description", ""
                    ),
                    "url": DOCUMENTATION_RESOURCES.get(r["uri"], {}).get("uri", ""),
                }
                for r in resources
            ],
        }
    )


@app.get("/api/mcp-client-config", name="api_mcp_client_config")
async def api_mcp_client_config(request: Request):
    mcp_env_loader()
    _host = request.headers.get("host", "localhost:9000").split(":")[0]
    _default_sse = f"http://{_host}:8000/sse"
    _env_sse = os.environ.get("MCP_SSE_URL", "")
    mcp_sse_url = (
        _env_sse
        if (_env_sse and _env_sse != "http://localhost:8000/sse")
        else _default_sse
    )
    return JSONResponse(
        {
            "mcp_sse_url": mcp_sse_url,
            "edge_url": os.environ.get("EDGE_URL", ""),
            "client_id": os.environ.get("EDGE_API_CLIENT_ID", ""),
            "client_secret": os.environ.get("EDGE_API_CLIENT_SECRET", ""),
            "edge_manager_url": os.environ.get("EDGE_MANAGER_URL", ""),
            "edge_api_token": os.environ.get("EDGE_API_TOKEN", ""),
            "edge_manager_project_id": os.environ.get("EDGE_MANAGER_PROJECT_ID", ""),
            "edge_manager_device_id": os.environ.get("EDGE_MANAGER_DEVICE_ID", ""),
            "nats_source": os.environ.get("NATS_SOURCE", ""),
            "nats_port": os.environ.get("NATS_PORT", "4222"),
            "nats_password": os.environ.get("NATS_PASSWORD", ""),
            "nats_tls": os.environ.get("NATS_TLS", "true"),
            "influx_host": os.environ.get("INFLUX_HOST", ""),
            "influx_port": os.environ.get("INFLUX_PORT", "8086"),
            "influx_db_name": os.environ.get("INFLUX_DB_NAME", "tsdata"),
            "influx_username": os.environ.get("INFLUX_USERNAME", ""),
            "influx_password": os.environ.get("INFLUX_PASSWORD", ""),
        }
    )


# ── Utility pages ──────────────────────────────────────────────────────────


@app.get("/health", response_class=HTMLResponse, name="health")
async def health_check(request: Request):
    mcp_env_loader()
    edge_instances = get_edge_instances()
    return templates.TemplateResponse(
        request,
        "health.html",
        {
            "status": "ok",
            "version": "1.0",
            "active_page": "health",
            "edge_instances": edge_instances,
        },
    )


def _run_health_checks(connection, base_url: str) -> dict:
    """Run all service health checks using the given connection and base URL."""
    import json as _json
    from litmussdk.utils.api import direct_request

    def _get(path):
        try:
            code, raw = direct_request(
                connection=connection, url=f"{base_url}{path}", request_type="GET"
            )
            try:
                data = _json.loads(raw) if raw else None
            except Exception:
                data = raw
            return code, data
        except Exception:
            return None, None

    def _ok(code):
        return code is not None and 200 <= code < 300

    def _ver(data):
        if not isinstance(data, dict):
            return "", ""
        version = (
            data.get("version")
            or data.get("Version")
            or data.get("tag")
            or data.get("appVersion")
            or ""
        )
        git = (
            data.get("git")
            or data.get("gitCommit")
            or data.get("git_commit")
            or data.get("commit")
            or data.get("hash")
            or ""
        )
        if isinstance(git, str) and len(git) > 8:
            git = git[:8]
        return (str(version) if version else ""), (str(git) if git else "")

    def _getver(path):
        c, d = _get(path)
        logger.info("version %s → code=%s data=%s", path, c, d)
        return c, d

    def _gql(path, query_str):
        try:
            body = _json.dumps({"query": query_str, "variables": {}})
            code, raw = direct_request(
                connection=connection,
                url=f"{base_url}{path}",
                request_type="POST",
                additional_body=body,
                extra_headers={"Content-Type": "application/json"},
            )
            try:
                data = _json.loads(raw) if raw else None
            except Exception:
                data = raw
            return code, data
        except Exception:
            return None, None

    services = {}
    host = {}

    code, data = _getver("/devicehub/version")
    v, g = _ver(data)
    services["devicehub"] = {
        "status": "ok" if _ok(code) else "error",
        "version": v,
        "git": g,
    }

    dt_code, dt_data = _gql(
        "/digital-twins", "query Version { Version { Git Version } }"
    )
    v, g = "", ""
    if isinstance(dt_data, dict):
        ver = (dt_data.get("data") or {}).get("Version") or {}
        v = str(ver.get("Version", "") or "")
        g = str(ver.get("Git", "") or "")
        if isinstance(g, str) and len(g) > 8:
            g = g[:8]
    services["digital_twins"] = {
        "status": "ok" if _ok(dt_code) else "error",
        "version": v,
        "git": g,
    }

    code, _ = _get("/flows-manager/flows")
    _, vdata = _getver("/flows-manager/version")
    v, g = _ver(vdata)
    services["flows_manager"] = {
        "status": "ok" if _ok(code) else "error",
        "version": v,
        "git": g,
    }

    code, data = _getver("/analytics/v2/version")
    v, g = _ver(data)
    services["analytics"] = {
        "status": "ok" if _ok(code) else "error",
        "version": v,
        "git": g,
    }

    code, data = _get("/apps/dc/containers/?all=true")
    _, vdata = _getver("/apps/version")
    v, g = _ver(vdata)
    services["marketplace"] = {
        "status": "ok" if _ok(code) else "error",
        "data": len(data) if isinstance(data, list) else None,
        "version": v,
        "git": g,
    }

    code, _ = _get("/cc/providers")
    _, vdata = _getver("/cc/version")
    v, g = _ver(vdata)
    services["integration"] = {
        "status": "ok" if _ok(code) else "error",
        "version": v,
        "git": g,
    }

    code, _ = _get("/opcua/service_conf")
    _, vdata = _getver("/opcua/version")
    v, g = _ver(vdata)
    services["opcua"] = {
        "status": "ok" if _ok(code) else "error",
        "version": v,
        "git": g,
    }

    code, data = _get("/dm/host/info")
    if isinstance(data, dict):
        host = data
    _, vdata = _getver("/dm/version")
    v, g = _ver(vdata)
    if not v and isinstance(data, dict):
        v = str(
            data.get("firmwareVersion")
            or data.get("osVersion")
            or data.get("kernelVersion")
            or data.get("version")
            or ""
        )
    services["system"] = {
        "status": "ok" if _ok(code) else "error",
        "version": v,
        "git": g,
    }

    hostname = (
        host.get("description")
        or host.get("hostname")
        or host.get("deviceName")
        or host.get("name")
        or ""
    )
    return {
        "status": "connected",
        "url": base_url,
        "hostname": hostname,
        "services": services,
    }


@app.get("/api/edge-health", name="api_edge_health")
async def api_edge_health(index: int = 0):
    mcp_env_loader()
    validate_cert = os.environ.get("VALIDATE_CERTIFICATE", "false").lower() == "true"

    # Determine connection type and parameters
    if index > 0:
        inst_type = os.environ.get(f"EDGE_INSTANCE_{index}_TYPE", "direct")
        inst_url = os.environ.get(f"EDGE_INSTANCE_{index}_URL", "").rstrip("/")
    else:
        inst_type = "lem" if os.environ.get("EDGE_MANAGER_URL", "") else "direct"
        inst_url = ""

    if inst_type == "lem":
        if index > 0:
            manager_url = inst_url
            api_token = os.environ.get(f"EDGE_INSTANCE_{index}_API_TOKEN", "")
            proj_id = os.environ.get(f"EDGE_INSTANCE_{index}_PROJECT_ID", "")
            dev_id = os.environ.get(f"EDGE_INSTANCE_{index}_DEVICE_ID", "")
        else:
            manager_url = os.environ.get("EDGE_MANAGER_URL", "").rstrip("/")
            api_token = os.environ.get("EDGE_API_TOKEN", "")
            proj_id = os.environ.get("EDGE_MANAGER_PROJECT_ID", "")
            dev_id = os.environ.get("EDGE_MANAGER_DEVICE_ID", "")

        if not manager_url or not api_token or not proj_id or not dev_id:
            return JSONResponse({"status": "not_configured"})

        bridge_base = f"{manager_url}/api/v1/edge/{proj_id}/{dev_id}"

        def _check():
            from litmussdk.utils.conn import new_lem_bridge_connection

            connection = new_lem_bridge_connection(
                edge_manager_url=manager_url,
                edge_api_token=api_token,
                project_id=proj_id,
                device_id=dev_id,
                validate_certificate=validate_cert,
                timeout_seconds=10,
            )
            return _run_health_checks(connection, bridge_base)

        try:
            result = await asyncio.to_thread(_check)
            return JSONResponse(result)
        except Exception:
            return JSONResponse({"status": "error"})

    # Direct connection path
    if index > 0:
        edge_url = inst_url
        client_id = os.environ.get(f"EDGE_INSTANCE_{index}_CLIENT_ID", "")
        client_secret = os.environ.get(f"EDGE_INSTANCE_{index}_SECRET", "")
    else:
        edge_url = os.environ.get("EDGE_URL", "").rstrip("/")
        client_id = os.environ.get("EDGE_API_CLIENT_ID", "")
        client_secret = os.environ.get("EDGE_API_CLIENT_SECRET", "")

    if not edge_url or not client_id or not client_secret:
        return JSONResponse({"status": "not_configured"})

    def _check():
        from litmussdk.utils.conn import new_le_connection

        connection = new_le_connection(
            edge_url=edge_url,
            client_id=client_id,
            client_secret=client_secret,
            validate_certificate=validate_cert,
            timeout_seconds=10,
        )
        return _run_health_checks(connection, edge_url)

    try:
        result = await asyncio.to_thread(_check)
        return JSONResponse(result)
    except Exception:
        return JSONResponse({"status": "error"})


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return RedirectResponse(url="/")
    if exc.status_code == 500:
        return templates.TemplateResponse(
            request, "500.html", status_code=500
        )
    return await http_exception_handler(request, exc)


if __name__ == "__main__":

    server_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "server.py"
    )
    server_proc = subprocess.Popen([sys.executable, server_script])

    # Wait until the MCP SSE server on port 8000 is accepting connections (max 10 s).
    import socket as _socket
    import time as _time

    _mcp_port = int(os.environ.get("MCP_PORT", 8000))
    for _ in range(20):
        try:
            with _socket.create_connection(("127.0.0.1", _mcp_port), timeout=0.5):
                break
        except OSError:
            _time.sleep(0.5)

    try:
        # Local operator console: bind loopback unless deliberately exposed.
        # The Docker image sets WEB_UI_HOST=0.0.0.0 so the -p port mapping
        # works; exposure there is still opt-in via -p 9000:9000.
        _ui_host = os.environ.get("WEB_UI_HOST", "127.0.0.1")
        uvicorn.run("web_client:app", host=_ui_host, port=9000, reload=False)
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.exception(f"Error running server: {e}")
    finally:
        server_proc.terminate()
        server_proc.wait()
