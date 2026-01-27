#!/bin/bash
set -e

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    source .env
fi

# Start the MCP server directly with Python
# The server uses uvicorn internally and runs on port 8000 by default
python3 src/server.py
