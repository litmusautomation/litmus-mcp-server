from typing import Optional, Iterable, cast
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters, stdio_client

from anthropic import Anthropic
from anthropic.types import MessageParam, ToolParam

from agents import Agent, Runner, gen_trace_id, trace
from agents.mcp import MCPServerStdio
from agents import ModelSettings

_default_anthropic_model = "claude-3-7-sonnet-20250219"
_anthropic_display_name = "Claude Sonnet"
_openai_display_name = "OpenAI GPT-4o"


class MCPClient:
    def __init__(self):
        # Initialize session and client objects
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.anthropic = None
        self.stdio = None
        self.write = None
        self.server_params = None
        self.model_used = None
        self.current_full_response = None

    async def connect_to_server(self, server_script_path: str):
        """Connect to an MCP server

        Args:
            server_script_path: Path to the server script (.py or .js)
        """
        if self.server_params is None:
            self.server_params = {}

        is_python = server_script_path.endswith(".py")
        is_js = server_script_path.endswith(".js")
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python" if is_python else "node"

        self.server_params["command"] = command
        self.server_params["args"] = [server_script_path]
        server_params = StdioServerParameters(
            command=command, args=[server_script_path], env=None
        )

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )

        await self.session.initialize()

        # List available tools
        response = await self.session.list_tools()
        tools = response.tools
        print("\nConnected to server with tools:", [tool.name for tool in tools])

    async def anthropic_creator(self, query, conversation_history):
        if self.anthropic is None:
            self.anthropic = Anthropic()

        # Use provided conversation history or create new messages list
        if conversation_history:
            messages = conversation_history.copy()
        else:
            messages = []

        # Add the user's current query
        messages.append({"role": "user", "content": query})

        response = await self.session.list_tools()
        available_tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema,
            }
            for tool in response.tools
        ]
        self.model_used = _anthropic_display_name

        return messages, available_tools

    async def process_query_anthropic(
        self,
        query: str,
        conversation_history=None,
        max_tokens: int = 4096,
        anthropic_model_used=None,
    ) -> str:
        """
        Process a query using Claude and available tools with conversation history

        Args:
            query: Query to process
            conversation_history: Optional list of previous messages
            max_tokens: Maximum tokens for response (default: 4096)
            anthropic_model_used: default model is claude-3-7-sonnet-20250219
        """
        model = anthropic_model_used or _default_anthropic_model
        messages, available_tools = await self.anthropic_creator(
            query, conversation_history
        )

        converted_messages = cast(Iterable[MessageParam], messages)
        converted_available_tools = cast(Iterable[ToolParam], available_tools)

        # Claude API call with conversation history
        response = self.anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=converted_messages,
            tools=converted_available_tools,
        )

        # Process response and handle tool calls
        final_text = []

        for content in response.content:
            if content.type == "text":
                final_text.append(content.text)
            elif content.type == "tool_use":
                tool_name = content.name
                tool_args = content.input
                try:
                    tool_args = vars(tool_args)
                except TypeError:
                    tool_args = tool_args

                # Execute tool call
                result = await self.session.call_tool(tool_name, tool_args)
                final_text.append(f"[Calling tool {tool_name} with args {tool_args}]")

                # Continue conversation with tool results
                if hasattr(content, "text") and content.text:
                    messages.append({"role": "assistant", "content": content.text})
                messages.append({"role": "user", "content": result.content})
                converted_messages = cast(Iterable[MessageParam], messages)

                # Get next response from Claude
                response = self.anthropic.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=converted_messages,
                    tools=converted_available_tools,
                )

                final_text.append(response.content[0].text)

        return "\n".join(final_text)

    async def chat_loop(self):
        """Run an interactive chat loop with conversation history (max 5 messages)"""
        conversation_history = []
        max_history = 5  # Keep only the last 5 message pairs (10 messages total)

        print("Interactive chat mode (type 'quit' to exit, 'clear' to reset history)")

        while True:
            try:
                query = input("\nQuery: ").strip()

                if query.lower() == "quit":
                    break
                elif query.lower() == "clear":
                    conversation_history = []
                    print("Conversation history cleared")
                    continue

                # Process the query with conversation history
                response = await self.process_query_anthropic(
                    query, conversation_history=conversation_history
                )
                print("\n" + response)

                # Update conversation history
                conversation_history.append({"role": "user", "content": query})
                conversation_history.append({"role": "assistant", "content": response})

                # Trim history to keep only the most recent message pairs
                if len(conversation_history) > max_history * 2:
                    conversation_history = conversation_history[-max_history * 2 :]

            except Exception as e:
                print(f"\nError: {str(e)}")

    async def process_streaming_query(
        self,
        query: str,
        conversation_history=None,
        max_tokens: int = 4096,
        anthropic_model_used=None,
    ):
        """
        Process a query using Claude with streaming responses.

        Args:
            query: Query to process
            conversation_history: Optional list of previous messages
            max_tokens: Maximum tokens for response
            anthropic_model_used: default model is claude-3-7-sonnet-20250219

        Yields:
            Chunks of text as they become available
        """
        model = anthropic_model_used or _default_anthropic_model
        messages, available_tools = await self.anthropic_creator(
            query, conversation_history
        )
        messages, available_tools = await self.anthropic_creator(
            query, conversation_history
        )

        # Initialize the full response tracker
        self.current_full_response = ""

        def stream_chunks():
            with self.anthropic.messages.stream(
                model=model,
                max_tokens=max_tokens,
                messages=messages,
                tools=available_tools,
            ) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            yield event.delta.text
                    elif event.type == "tool_use":
                        yield f"\n[Tool requested: {event.name}]\n"

        for chunk in stream_chunks():
            self.current_full_response += chunk
            yield chunk

    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()

    async def process_query_with_openai_agent(
        self, query: str, conversation_history=None
    ):
        if conversation_history:
            messages = conversation_history.copy()
        else:
            messages = []
        messages.append({"role": "user", "content": query})

        async with MCPServerStdio(
            name="StdioServer",
            params=self.server_params,
            client_session_timeout_seconds=300,
        ) as server:
            trace_id = gen_trace_id()
            with trace(workflow_name="StdioServer", trace_id=trace_id):
                print(
                    f"View trace: https://platform.openai.com/traces/trace?trace_id={trace_id}\n"
                )
                agent = Agent(
                    name="Assistant",
                    instructions="Respond to the question to the best of abilities. Use the appropriate tools to solve the problem, if any.",
                    mcp_servers=[server],
                    model_settings=ModelSettings(tool_choice="required"),
                )
                result = await Runner.run(starting_agent=agent, input=messages)
                self.model_used = _openai_display_name

        return result
