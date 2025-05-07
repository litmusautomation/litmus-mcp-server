#!/bin/bash
set -e

# Start server
mcp run src/server.py --transport=sse &
SERVER_PID=$!

# Start web client
python src/web_client.py src/server.py &
CLIENT_PID=$!

# Wait for both
wait $SERVER_PID $CLIENT_PID
