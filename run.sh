#!/bin/bash
set -e

if [ -f .env ]; then
    source .env
fi

# Prefer the project venv Python (always present in Docker, present locally after uv sync).
# This also ensures server.py (spawned as subprocess) uses the same venv via sys.executable.
if [ -f ".venv/bin/python3" ]; then
    exec .venv/bin/python3 src/web_client.py
else
    exec python3 src/web_client.py
fi
