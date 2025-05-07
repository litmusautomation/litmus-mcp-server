import asyncio
from utils import mcp_env_loader
from client_utils import MCPClient

mcp_env_loader()


async def main():
    if len(sys.argv) < 2:
        print("Usage: python cli_client.py <path_to_server_script>")
        sys.exit(1)

    client = MCPClient()
    try:
        await client.connect_to_server(sys.argv[1])
        await client.chat_loop()
    finally:
        await client.cleanup()


if __name__ == "__main__":
    import sys

    asyncio.run(main())
