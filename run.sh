#!/bin/bash
set -e

# Start the MCP server directly with Python
# The server uses uvicorn internally and runs on port 8000 by default
python src/server.py
