# TODO

## Chat Plot Rendering

When the user asks the AI to plot data, render an actual image instead of markdown text.

### Plan

1. **`pyproject.toml`** — add `matplotlib>=3.9.0` dependency

2. **`src/tools/plot_tools.py`** — new MCP tool `generate_plot` that:
   - Accepts: `values` (array), `timestamps` (array, optional), `title`, `y_label`, `x_label`, `chart_type` (line/bar/scatter)
   - Generates a styled matplotlib PNG (Agg backend, no display required)
   - Saves to `/tmp/litmus-plots/{uuid}.png`
   - Returns JSON: `{"success": true, "plot_url": "/plots/{uuid}.png", "title": ..., ...}`
   - Style to match app palette: primary `#4fb896`, bg `#f8faf9`

3. **`src/server.py`** — register the tool:
   - Import `generate_plot_tool` from `tools.plot_tools`
   - Add `Tool(name="generate_plot", ...)` to `get_tool_definitions()`
   - Add `elif name == "generate_plot"` routing in `handle_call_tool()`

4. **`src/web_client.py`** — add `/plots/{filename}` route:
   - Import `FileResponse` from `fastapi.responses`
   - Serve `.png` files from `/tmp/litmus-plots/` (reject path traversal)

5. **`src/client_utils.py`** — auto-inject image markdown after plot tool calls:
   - Add `import json` at top
   - In `process_streaming_query`, after `result_text` is assembled, parse JSON and check for `plot_url`
   - If present: `yield f"\n![{title}]({plot_url})\n"` so the image appears inline without relying on LLM to output it

### How it works end-to-end

1. User: "plot the temperature data"
2. LLM calls `get_multiple_values_from_topic` → gets values + timestamps
3. LLM calls `generate_plot` with the data
4. Tool saves PNG to `/tmp/litmus-plots/uuid.png`, returns JSON with `plot_url`
5. `client_utils.py` detects `plot_url` in result, yields `\n![title](/plots/uuid.png)\n`
6. `marked.parse()` in the frontend renders the `<img>` tag
7. Browser loads `/plots/uuid.png` from the web client (port 9000)
