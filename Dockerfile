FROM python:3.12-slim
LABEL authors="Litmus Automation, Inc."


RUN apt-get update && apt-get install -y \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
COPY --from=ghcr.io/astral-sh/uv:latest /uvx /bin/uvx
WORKDIR /app

COPY . .
RUN uv venv && uv sync --all-groups

ENV PATH="/app/.venv/bin:$PATH"

#CMD python src/server.py --transport=sse & python src/webclient.py src/server.py && wait
RUN chmod +x run.sh
CMD ["./run.sh"]