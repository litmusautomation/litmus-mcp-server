FROM python:3.12-slim
LABEL authors="Litmus Automation, Inc."
LABEL description="Litmus MCP Server - SSE-based Model Context Protocol server for Litmus Edge"

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
COPY --from=ghcr.io/astral-sh/uv:latest /uvx /bin/uvx

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY templates ./templates
COPY static ./static
COPY run.sh ./

# Install dependencies using uv
RUN uv venv && uv sync --frozen --group llm-sdks

# Activate virtual environment
ENV PATH="/app/.venv/bin:$PATH"

# Expose the MCP server port (default 8000) and the web UI port (default 9000)
EXPOSE 8000 9000

# Make run script executable
RUN chmod +x run.sh

# Run the server
CMD ["./run.sh"]