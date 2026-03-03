# Litmus MCP Server — Claude Agent Guide

## What This Project Is
Official Litmus Automation MCP server for Litmus Edge (industrial IoT). Two processes:
- **MCP server** — `src/server.py`, port 8000, SSE transport, exposes tools to LLM clients
- **Web client** — `src/web_client.py`, port 9000, FastAPI chat UI + config pages

Started together via `run.sh` → `web_client.py` launches `server.py` as subprocess.

---

## Critical File Map

| File | Role |
|------|------|
| `src/server.py` | Tool definitions (`get_tool_definitions()`), routing (`handle_call_tool()`), SSE setup |
| `src/web_client.py` | All web routes: `/`, `/chat`, `/update-env`, `/health`, API endpoints |
| `src/client_utils.py` | LLM streaming (`process_streaming_query`), tool call loop, OpenAI agent |
| `src/env_config.py` | `.env` read/write, `mcp_env_loader()`, `mcp_env_updater()`, path constants |
| `src/config.py` | Ports, SSL config, NATS/InfluxDB defaults |
| `src/utils/auth.py` | `get_nats_connection_params()`, `get_litmus_connection()`, `get_influx_connection_params()` |
| `src/utils/formatting.py` | `format_success_response()`, `format_error_response()` |
| `src/tools/data_tools.py` | NATS subscribe (single + multi), InfluxDB historical query |
| `src/tools/devicehub_tools.py` | Driver list, device CRUD, tag reads via litmussdk |
| `src/tools/dm_tools.py` | Friendly name get/set, cloud activation status |
| `src/tools/marketplace_tools.py` | Docker container list + run |
| `src/tools/digitaltwins_tools.py` | DT models, instances, attributes, hierarchy |
| `src/tools/resource_tools.py` | MCP Resources — fetches live docs.litmus.io |
| `src/conversation.py` | In-memory session history (max 5 pairs, keyed by cookie) |
| `templates/query.html` | Chat UI — XHR streaming, markdown rendering, tool badges, stop button |
| `templates/update_env.html` | Config page — API keys, model selector, edge instances, NATS/InfluxDB settings |
| `templates/setup.html` | First-run API key entry |
| `static/style.css` | All styles — chat, config, health, panels |
| `static/mcp-panels.js` | Floating Tools/Resources side panels |
| `.env` | Runtime config — written by UI, mounted via Docker volume for persistence |
| `pyproject.toml` | Dependencies (uv), litmussdk wheel URL |

---

## Architecture Essentials

**Request flow (chat):**
`POST /chat` → `anthropic_stream()` → `client_utils.process_streaming_query()` → `anthropic.messages.stream()` → tool calls via `session.call_tool()` → streamed chunks → `xhr.onprogress` → `marked.parse()`

**Tool call flow:**
`client_utils` calls MCP session → `server.py handle_call_tool()` → routes to `tools/*.py` function → returns `list[TextContent]` with JSON

**Auth flow:**
Every tool receives `request: Request` → extracts headers (EDGE_URL, credentials, NATS/InfluxDB params) via `utils/auth.py`

**Headers the web client sends to MCP server:**
`EDGE_URL`, `EDGE_API_CLIENT_ID`, `EDGE_API_CLIENT_SECRET`, `NATS_SOURCE`, `NATS_PORT`, `NATS_USER`, `NATS_PASSWORD`, `NATS_TLS`, `INFLUX_HOST`, `INFLUX_PORT`, `INFLUX_DB_NAME`, `INFLUX_USERNAME`, `INFLUX_PASSWORD`

**Multi-instance edge:**
`.env` stores `EDGE_INSTANCE_{i}_{URL|CLIENT_ID|SECRET|NAME}`, `ACTIVE_EDGE_INSTANCE=i`. Active instance's credentials always mirror `EDGE_URL/EDGE_API_CLIENT_ID/EDGE_API_CLIENT_SECRET`.

**litmussdk note:**
SDK uses Pydantic v2 validators that require `le_connection` passed via `model_validate(data, context={"le_connection": conn})`. Without context, it falls back to env vars and breaks. SDK wheel version: 2.5.3.

---

## Subagent Strategy

**Always parallelize independent file reads** — never read files sequentially if they don't depend on each other.

### Task → Agent Assignment

| Task | Approach |
|------|----------|
| UI change (template + CSS) | Read both files in parallel, edit directly — no Explore needed |
| Add a new MCP tool | Read `server.py` (routing section) + target `tools/*.py` in parallel; write tool file, then edit server.py |
| Fix a backend bug | Read the specific file(s) from the map above — skip Explore |
| Unknown territory / broad search | Use **Explore** agent with `thoroughness=quick` first |
| Multi-file feature (3+ files) | Use **Plan** agent to design before touching code |
| Research (docs, web) | Use **general-purpose** agent in background |

### Parallel Read Pattern
When starting any task, issue all required file reads in one message:
```
Read file A + Read file B + Read file C  (all in parallel)
→ then make edits
```

### Subagent Boundaries
- **Frontend-only changes** (templates + CSS): read template + style.css, edit directly
- **New tool**: `plot_tools.py` style — self-contained file + register in server.py (2 edits total)
- **Config/env changes**: always touch `web_client.py` (mcp_config dict) + `update_env.html` (HTML + JS) together
- **Auth/connection changes**: `src/utils/auth.py` only; propagate `use_tls`/params through call chain

---

## Adding a New Tool (Checklist)

1. Create `src/tools/{name}_tools.py` — async function `{name}_tool(request, arguments) -> list[TextContent]`
2. In `src/server.py`:
   - Add import at top with other tool imports
   - Add `Tool(name=..., description=..., inputSchema=...)` inside `get_tool_definitions()` return list
   - Add `elif name == "..."` branch in `handle_call_tool()`
3. All tools use `format_success_response(dict)` or `format_error_response(code, msg)` from `utils/formatting.py`
4. Raise `McpError(ErrorData(code=INVALID_PARAMS, message=...))` for bad inputs

---

## Code Quality Rules

- **Read before editing** — never modify a file you haven't read in this session
- **No speculative code** — only implement what was asked; no extra error handling, no future-proofing
- **No new abstractions for one-off use** — three similar lines beats a premature helper
- **Parallelize all independent reads** — sequential reads for independent files waste time
- **Security** — validate/sanitize all user input at boundaries; no path traversal in file-serving routes
- **NATS** — `allow_reconnect=False` always; `use_tls` from `get_nats_connection_params()`, default `True`; wrap `nc.drain()` in try/except with `nc.close()` fallback
- **Streaming** — `GeneratorExit` is caught by `finally` in `anthropic_stream()`; no special handling needed
- **litmussdk** — always pass `context={"le_connection": conn}` to `model_validate()`

---

## Open TODOs

See `.claude/TODO.md` for full specs.

### Chat Plot Rendering
When user asks AI to plot data, generate an actual matplotlib image instead of markdown table.
Files: `pyproject.toml` (add matplotlib), `src/tools/plot_tools.py` (new), `src/server.py` (register), `src/web_client.py` (add `/plots/{filename}` route), `src/client_utils.py` (auto-inject image markdown on `plot_url` in tool result).
