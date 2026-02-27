import os
from typing import Optional, Iterable, cast
from contextlib import AsyncExitStack, asynccontextmanager
from mcp import ClientSession
from mcp.client.sse import sse_client

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam

from agents import Agent, Runner, gen_trace_id, trace
from agents.mcp import MCPServerSse
from agents import ModelSettings

_anthropic_display_name = "Claude Sonnet 4.6"
_openai_display_name = "OpenAI GPT-4.1"
_gemini_display_name = "Google Gemini"

_CREDENTIAL_KEYS = (
    "EDGE_URL",
    "EDGE_API_CLIENT_ID",
    "EDGE_API_CLIENT_SECRET",
    "VALIDATE_CERTIFICATE",
    "NATS_SOURCE",
    "NATS_PORT",
    "NATS_USER",
    "NATS_PASSWORD",
    "INFLUX_HOST",
    "INFLUX_PORT",
    "INFLUX_DB_NAME",
    "INFLUX_USERNAME",
    "INFLUX_PASSWORD",
)


def _get_model_id(provider: str) -> str:
    preferred = os.environ.get("PREFERRED_MODEL_ID", "")
    if preferred:
        return preferred
    if provider == "anthropic":
        return "claude-sonnet-4-6"
    if provider == "gemini":
        return "gemini-2.0-flash"
    return "gpt-4.1"

_system_prompt = (
    "You are a helpful assistant for Litmus Edge, an industrial IoT platform. "
    "You have access to tools for querying devices, data streams, tags, and configuration. "
    "Use tools when they are relevant to the user's question. "
    "Respond directly for general questions or conversation that does not require tool use."
)


class MCPClient:
    def __init__(self):
        self.anthropic: Optional[AsyncAnthropic] = None
        self._anthropic_key: str = ""
        self.model_used = None

    @asynccontextmanager
    async def _open_session(self):
        """Open a fresh SSE session using current env credentials."""
        url = os.environ.get("MCP_SSE_URL", "http://localhost:8000/sse")
        headers = {k: v for k in _CREDENTIAL_KEYS if (v := os.environ.get(k, ""))}
        async with AsyncExitStack() as stack:
            transport = await stack.enter_async_context(sse_client(url=url, headers=headers))
            read, write = transport
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            yield session

    def _ensure_anthropic(self):
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if self.anthropic is None or key != self._anthropic_key:
            self.anthropic = AsyncAnthropic()
            self._anthropic_key = key

    async def _list_tools(self):
        async with self._open_session() as session:
            response = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in response.tools
        ]

    async def _list_resources(self):
        async with self._open_session() as session:
            response = await session.list_resources()
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

        async with self._open_session() as session:
            tools_resp = await session.list_tools()
            converted_tools = cast(
                Iterable[ToolParam],
                [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.inputSchema,
                    }
                    for t in tools_resp.tools
                ],
            )
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

                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in tool_uses:
                    tool_args = dict(block.input) if block.input else {}
                    result = await session.call_tool(block.name, tool_args)
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

        async with self._open_session() as session:
            tools_resp = await session.list_tools()
            available_tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.inputSchema,
                }
                for t in tools_resp.tools
            ]

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

                messages.append({"role": "assistant", "content": final.content})
                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        yield f"\n[Tool: {block.name}]\n"
                        tool_args = dict(block.input) if block.input else {}
                        result = await session.call_tool(block.name, tool_args)
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

    async def process_streaming_query_gemini(
        self, query: str, conversation_history=None, max_tokens: int = 4096
    ):
        """
        Stream a Gemini response, handling tool calls mid-stream.

        Yields plain text chunks and \\n[Tool: <name>]\\n sentinel lines.
        """
        from google import genai
        from google.genai import types

        self.model_used = _gemini_display_name

        api_key = os.environ.get("GEMINI_API_KEY", "")
        client = genai.Client(api_key=api_key)

        contents = []
        if conversation_history:
            for msg in conversation_history:
                role = "model" if msg["role"] == "assistant" else "user"
                contents.append(
                    types.Content(role=role, parts=[types.Part(text=msg["content"])])
                )
        contents.append(types.Content(role="user", parts=[types.Part(text=query)]))

        async with self._open_session() as session:
            tools_resp = await session.list_tools()
            function_declarations = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description or "",
                    parameters=t.inputSchema,
                )
                for t in tools_resp.tools
            ]
            gemini_tools = [types.Tool(function_declarations=function_declarations)]

            while True:
                function_calls = []
                assistant_parts = []

                async for chunk in await client.aio.models.generate_content_stream(
                    model=_get_model_id("gemini"),
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=_system_prompt,
                        tools=gemini_tools,
                        max_output_tokens=max_tokens,
                    ),
                ):
                    if not chunk.candidates:
                        continue
                    for part in chunk.candidates[0].content.parts:
                        if part.text:
                            assistant_parts.append(part)
                            yield part.text
                        elif part.function_call:
                            assistant_parts.append(part)
                            function_calls.append(part.function_call)

                if not function_calls:
                    break

                contents.append(types.Content(role="model", parts=assistant_parts))

                response_parts = []
                for fc in function_calls:
                    yield f"\n[Tool: {fc.name}]\n"
                    tool_args = dict(fc.args) if fc.args else {}
                    result = await session.call_tool(fc.name, tool_args)
                    result_text = "\n".join(
                        rc.text for rc in result.content if hasattr(rc, "text")
                    )
                    response_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"result": result_text},
                        )
                    )

                contents.append(types.Content(role="user", parts=response_parts))

    async def cleanup(self):
        pass  # No persistent resources; kept for interface compatibility

    async def process_query_with_openai_agent(
        self, query: str, conversation_history=None
    ):
        messages = list(conversation_history) if conversation_history else []
        messages.append({"role": "user", "content": query})

        url = os.environ.get("MCP_SSE_URL", "http://localhost:8000/sse")
        headers = {k: v for k in _CREDENTIAL_KEYS if (v := os.environ.get(k, ""))}

        async with MCPServerSse(
            name="SseServer",
            params={"url": url, "headers": headers},
        ) as server:
            trace_id = gen_trace_id()
            with trace(workflow_name="SseServer", trace_id=trace_id):
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
