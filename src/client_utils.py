import os
from typing import Optional, Iterable, cast
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters, stdio_client

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam

from agents import Agent, Runner, gen_trace_id, trace
from agents.mcp import MCPServerStdio
from agents import ModelSettings

_anthropic_display_name = "Claude Sonnet 4.6"
_openai_display_name = "OpenAI GPT-4.1"


def _get_model_id(provider: str) -> str:
    preferred = os.environ.get("PREFERRED_MODEL_ID", "")
    if preferred:
        return preferred
    return "claude-sonnet-4-6" if provider == "anthropic" else "gpt-4.1"

_system_prompt = (
    "You are a helpful assistant for Litmus Edge, an industrial IoT platform. "
    "You have access to tools for querying devices, data streams, tags, and configuration. "
    "Use tools when they are relevant to the user's question. "
    "Respond directly for general questions or conversation that does not require tool use."
)


class MCPClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.anthropic: Optional[AsyncAnthropic] = None
        self._anthropic_key: str = ""
        self.stdio = None
        self.write = None
        self.server_params = None
        self.model_used = None

    async def connect_to_server(self, server_script_path: str):
        if self.server_params is None:
            self.server_params = {}

        is_python = server_script_path.endswith(".py")
        is_js = server_script_path.endswith(".js")
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python" if is_python else "node"
        # Pass only the keys the MCP subprocess needs — avoids leaking unrelated
        # shell vars while ensuring credentials written by mcp_env_loader() /
        # activate_edge_instance() are forwarded to the subprocess.
        _MCP_ENV_KEYS = (
            "EDGE_URL", "EDGE_API_CLIENT_ID", "EDGE_API_CLIENT_SECRET",
            "VALIDATE_CERTIFICATE",
            "NATS_SOURCE", "NATS_PORT", "NATS_USER", "NATS_PASSWORD",
            "INFLUX_HOST", "INFLUX_PORT", "INFLUX_DB_NAME",
            "INFLUX_USERNAME", "INFLUX_PASSWORD",
            "PATH", "HOME", "PYTHONPATH",  # needed to locate python + packages
        )
        current_env = {
            k: v for k, v in os.environ.items()
            if k in _MCP_ENV_KEYS or k.startswith("EDGE_INSTANCE_")
        }
        self.server_params["command"] = command
        self.server_params["args"] = [server_script_path, "--stdio"]
        self.server_params["env"] = current_env

        server_params = StdioServerParameters(
            command=command, args=[server_script_path, "--stdio"], env=current_env
        )

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )

        await self.session.initialize()

        response = await self.session.list_tools()
        print("\nConnected to server with tools:", [t.name for t in response.tools])

    def _ensure_anthropic(self):
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if self.anthropic is None or key != self._anthropic_key:
            self.anthropic = AsyncAnthropic()
            self._anthropic_key = key

    async def _list_tools(self):
        response = await self.session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in response.tools
        ]

    async def _list_resources(self):
        response = await self.session.list_resources()
        return [
            {
                "uri": str(r.uri),
                "name": r.name,
                "description": r.description or "",
            }
            for r in response.resources
        ]

    async def process_query_anthropic(
        self, query: str, conversation_history=None, max_tokens: int = 4096
    ) -> str:
        self._ensure_anthropic()
        self.model_used = _anthropic_display_name

        messages = list(conversation_history) if conversation_history else []
        messages.append({"role": "user", "content": query})

        available_tools = await self._list_tools()
        converted_tools = cast(Iterable[ToolParam], available_tools)
        final_text = []

        response = await self.anthropic.messages.create(
            model=_get_model_id("anthropic"),
            max_tokens=max_tokens,
            system=_system_prompt,
            messages=cast(Iterable[MessageParam], messages),
            tools=converted_tools,
        )

        while True:
            text_parts = []
            tool_uses = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_uses.append(block)

            if text_parts:
                final_text.extend(text_parts)

            if response.stop_reason != "tool_use" or not tool_uses:
                break

            # Execute all tool calls and continue the conversation
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in tool_uses:
                tool_args = dict(block.input) if block.input else {}
                result = await self.session.call_tool(block.name, tool_args)
                result_text = "\n".join(
                    rc.text for rc in result.content if hasattr(rc, "text")
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            messages.append({"role": "user", "content": tool_results})

            response = await self.anthropic.messages.create(
                model=_get_model_id("anthropic"),
                max_tokens=max_tokens,
                messages=cast(Iterable[MessageParam], messages),
                tools=converted_tools,
            )

        return "\n".join(final_text)

    async def process_streaming_query(
        self, query: str, conversation_history=None, max_tokens: int = 4096
    ):
        """
        Stream a response, properly handling tool calls mid-stream.

        Yields plain text chunks. Tool-call events are yielded as the
        sentinel line  \\n[Tool: <name>]\\n  so the client can style them.
        """
        self._ensure_anthropic()
        self.model_used = _anthropic_display_name

        messages = list(conversation_history) if conversation_history else []
        messages.append({"role": "user", "content": query})

        available_tools = await self._list_tools()

        while True:
            async with self.anthropic.messages.stream(
                model=_get_model_id("anthropic"),
                max_tokens=max_tokens,
                system=_system_prompt,
                messages=cast(Iterable[MessageParam], messages),
                tools=cast(Iterable[ToolParam], available_tools),
            ) as stream:
                async for text in stream.text_stream:
                    yield text

                final = await stream.get_final_message()

            if final.stop_reason != "tool_use":
                break

            # Execute every tool call, then continue streaming
            messages.append({"role": "assistant", "content": final.content})
            tool_results = []
            for block in final.content:
                if block.type == "tool_use":
                    yield f"\n[Tool: {block.name}]\n"
                    tool_args = dict(block.input) if block.input else {}
                    result = await self.session.call_tool(block.name, tool_args)
                    result_text = "\n".join(
                        rc.text for rc in result.content if hasattr(rc, "text")
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

            messages.append({"role": "user", "content": tool_results})
            # Loop: stream the follow-up response after tool results

    async def cleanup(self):
        await self.exit_stack.aclose()

    async def process_query_with_openai_agent(
        self, query: str, conversation_history=None
    ):
        messages = list(conversation_history) if conversation_history else []
        messages.append({"role": "user", "content": query})

        async with MCPServerStdio(
            name="StdioServer",
            params=self.server_params,
        ) as server:
            trace_id = gen_trace_id()
            with trace(workflow_name="StdioServer", trace_id=trace_id):
                agent = Agent(
                    name="Assistant",
                    instructions=_system_prompt,
                    model=_get_model_id("openai"),
                    mcp_servers=[server],
                    model_settings=ModelSettings(tool_choice="auto"),
                )
                result = await Runner.run(starting_agent=agent, input=messages)
                self.model_used = _openai_display_name

        return result
