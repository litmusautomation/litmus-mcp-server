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

# Install the standalone litmus-cli binary (backs the litmus_sdk_discover,
# litmus_sdk_read, and litmus_sdk_write fallback tools), pinned and
# checksum-verified
ARG LITMUS_CLI_VERSION=cli-v0.6.0
ARG TARGETARCH=amd64
RUN curl -fsSL -o /tmp/SHA256SUMS \
        "https://github.com/litmusautomation/litmus-sdk-releases/releases/download/${LITMUS_CLI_VERSION}/SHA256SUMS" \
    && curl -fsSL -o "/tmp/litmus-cli-linux-${TARGETARCH}" \
        "https://github.com/litmusautomation/litmus-sdk-releases/releases/download/${LITMUS_CLI_VERSION}/litmus-cli-linux-${TARGETARCH}" \
    && (cd /tmp && grep "litmus-cli-linux-${TARGETARCH}$" SHA256SUMS | sha256sum -c -) \
    && mv "/tmp/litmus-cli-linux-${TARGETARCH}" /usr/local/bin/litmus-cli \
    && chmod +x /usr/local/bin/litmus-cli \
    && rm /tmp/SHA256SUMS

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