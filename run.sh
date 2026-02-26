#!/bin/bash
set -e

if [ -f .env ]; then
    source .env
fi

exec python3 src/web_client.py
