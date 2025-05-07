import sys
import logging
import asyncio
from typing import AsyncGenerator

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.status import HTTP_303_SEE_OTHER
import uvicorn

from utils import (
    mcp_env_loader,
    mcp_env_updater,
    key_of_anthropic_api_key,
    get_current_mcp_env,
    mcp_env_remover,
    check_model_key,
    get_conversation_history,
    update_conversation_history,
    get_chat_log,
    check_streaming_status,
    MODEL_NAME_ANTHROPIC,
    key_of_openai_api_key,
    MODEL_PREFERENCE,
    MODEL_NAME_OPENAI,
    markdown_to_html,
)
from utils import JINJA_TEMPLATE_DIR, STATIC_DIR
from client_utils import MCPClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("web_client")

SERVER_SCRIPT = sys.argv[1] if len(sys.argv) > 1 else "server.py"
mcp_env_loader()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MCP client connection")
    client = MCPClient()
    await client.connect_to_server(SERVER_SCRIPT)
    app.state.client = client  # type: ignore[arg-type]

    yield
    logger.info("Cleaning up MCP client")
    await client.cleanup()

    logger.info("MCP client cleaned up")


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=JINJA_TEMPLATE_DIR)
templates.env.filters["markdown_to_html"] = markdown_to_html
app.mount(STATIC_DIR, StaticFiles(directory=STATIC_DIR), name="static")
app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.api_route(
    "/setup", methods=["GET", "POST"], response_class=HTMLResponse, name="setup"
)
async def setup(request: Request):
    if request.method == "GET":
        return templates.TemplateResponse("setup.html", {"request": request})

    form = await request.form()
    value_of_anthropic_api_key = form.get("value_of_anthropic_api_key", "").strip()
    value_of_openai_api_key = form.get("value_of_openai_api_key", "").strip()
    if not value_of_anthropic_api_key and not value_of_openai_api_key:
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "error": "Please provide at least one API key.",
            },
        )
    if value_of_anthropic_api_key:
        mcp_env_updater(key_of_anthropic_api_key, value_of_anthropic_api_key)
    if value_of_openai_api_key:
        mcp_env_updater(key_of_openai_api_key, value_of_openai_api_key)

    return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)


@app.post("/switch-model", response_class=HTMLResponse, name="switch_model")
async def switch_model(request: Request, switch_model_to: str = Form(...)):
    if switch_model_to.startswith("anthropic"):
        preference = MODEL_NAME_ANTHROPIC
    else:
        preference = MODEL_NAME_OPENAI
    mcp_env_updater(MODEL_PREFERENCE, preference)
    mcp_env_loader()
    return RedirectResponse("/update-env?updated=true", status_code=HTTP_303_SEE_OTHER)


@app.get("/update-env", response_class=HTMLResponse, name="update_env_form")
async def update_env_form(request: Request):
    current_env, _ = get_current_mcp_env()
    _, model_type = check_model_key()
    updated = request.query_params.get("updated") == "true"
    return templates.TemplateResponse(
        "update_env.html",
        {
            "request": request,
            "env": current_env,
            "updated": updated,
            "current_model": model_type,
        },
    )


@app.post("/update-env", response_class=HTMLResponse)
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
    # Clear the conversation history through the util function
    update_conversation_history(None, None, clear=True)
    return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)


@app.api_route(
    "/", methods=["GET", "POST"], response_class=HTMLResponse, name="query_handler"
)
async def query_handler(request: Request):
    ## If Stream-only mode
    # redirect_flag, redirect_url = check_streaming_status(current_route=request.url.path)
    # if redirect_flag:
    #     return RedirectResponse(redirect_url, status_code=HTTP_303_SEE_OTHER)

    mcp_env_loader()
    client = getattr(request.app.state, "client", None)
    if not client:
        logger.error("MCP client not initialized")
        raise HTTPException(
            status_code=500, detail="Internal server error: MCP client not initialized"
        )
    key_exists, model_type = check_model_key()
    if not key_exists:
        logger.info("Model key not found, redirecting to setup")
        return RedirectResponse("/setup", status_code=HTTP_303_SEE_OTHER)

    # Get conversation history
    conversation_history = get_conversation_history()
    chat_log = get_chat_log(conversation_history)

    if request.method == "POST":
        form = await request.form()
        query = form.get("query", "").strip()

        # Process query with conversation history
        if model_type == MODEL_NAME_ANTHROPIC:
            response_text = await client.process_query_anthropic(
                query, conversation_history=conversation_history
            )
        else:
            full_response_text = await client.process_query_with_openai_agent(
                query, conversation_history=conversation_history
            )
            response_text = full_response_text.final_output

        # Update conversation history
        update_conversation_history(query, response_text)

        # Add current exchange to chat log for display
        chat_log.append({"user": query, "assistant": response_text})
    else:
        query = ""
        response_text = ""

    return templates.TemplateResponse(
        "query.html",
        {
            "request": request,
            "query": query,
            "response_text": response_text,
            "chat_log": [
                {
                    "user": entry.get("user", ""),
                    "assistant": entry.get("assistant", ""),
                    "model": entry.get("model", client.model_used or model_type),
                }
                for entry in chat_log
            ],
            "has_history": len(chat_log) > 0,
        },
    )


## Streaming
@app.api_route("/streaming", methods=["GET", "POST"], name="streaming_query_handler")
async def streaming_query_handler(request: Request):
    redirect_flag, redirect_url = check_streaming_status(current_route=request.url.path)
    if redirect_flag:
        return RedirectResponse(redirect_url, status_code=HTTP_303_SEE_OTHER)

    mcp_env_loader()
    client = getattr(request.app.state, "client", None)
    if not client:
        logger.error("MCP client not initialized")
        if request.method == "POST":
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error: MCP client not initialized"},
            )
        raise HTTPException(
            status_code=500, detail="Internal server error: MCP client not initialized"
        )
    key_exists, model_type = check_model_key()
    ## TODO remove anthropic enforcement
    if not key_exists and model_type != MODEL_NAME_ANTHROPIC:
        logger.info("Model key not found, redirecting to setup")
        return RedirectResponse("/setup", status_code=HTTP_303_SEE_OTHER)

    # Get conversation history
    conversation_history = get_conversation_history()
    chat_log = get_chat_log(conversation_history)

    if request.method == "POST":
        try:
            form = await request.form()
            query = form.get("query", "").strip()

            if not query:
                return JSONResponse(
                    status_code=400, content={"error": "Query cannot be empty"}
                )

            async def stream_response() -> AsyncGenerator[str, None]:
                full_response = ""
                try:
                    # Set up SSE-style headers
                    yield "Content-Type: text/event-stream\r\n"
                    yield "Cache-Control: no-cache\r\n"
                    yield "\r\n"

                    async for chunk in client.process_streaming_query(
                        query, conversation_history=conversation_history
                    ):
                        if chunk:  # Only process non-empty chunks
                            full_response += chunk
                            yield chunk
                            # Add a small delay to prevent overwhelming the browser
                            await asyncio.sleep(0.01)

                    update_conversation_history(query, full_response)
                except Exception as e:
                    logger.exception(f"Error in streaming response: {str(e)}")
                    error_msg = f"Error: {str(e)}"
                    yield error_msg
                    if hasattr(client, "current_full_response"):
                        client.current_full_response += error_msg

            return StreamingResponse(
                stream_response(),
                media_type="text/plain",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        except Exception as e:
            logger.exception(f"Error setting up streaming: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"error": f"Error setting up streaming: {str(e)}"},
            )

    return templates.TemplateResponse(
        "query_streaming.html",
        {
            "request": request,
            "chat_log": [
                {
                    "user": entry.get("user", ""),
                    "assistant": entry.get("assistant", ""),
                }
                for entry in chat_log
            ],
            "model": client.model_used or model_type,
            "has_history": len(chat_log) > 0,
        },
    )


@app.get("/health", response_class=HTMLResponse, name="health")
async def health_check(request: Request):
    return templates.TemplateResponse(
        "health.html", {"request": request, "status": "ok", "version": "1.0"}
    )


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return RedirectResponse(url="/")
    elif exc.status_code == 500:
        return templates.TemplateResponse(
            "500.html", {"request": request}, status_code=500
        )
    return await http_exception_handler(request, exc)


if __name__ == "__main__":
    import webbrowser

    try:
        webbrowser.open("http://localhost:9000")
        uvicorn.run("web_client:app", host="0.0.0.0", port=9000, reload=True)
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.exception(f"Error running server: {str(e)}")
