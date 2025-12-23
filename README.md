<p align="center">
  <a href="https://litmus.io">
    <picture>
      <source media="(prefers-color-scheme: light)" srcset="static/litmus-logo-light.svg" />
      <source media="(prefers-color-scheme: dark)" srcset="static/litmus-logo-dark.svg" />
      <img src="static/litmus-logo-light.svg" height="60" alt="Litmus logo" />
    </picture>
  </a>
</p>

<p align="center">
  <a href="https://docs.litmus.io">
    <img src="https://img.shields.io/badge/Litmus-Docs-2acfa6?style=flat-square" alt="Documentation" />
  </a>
  <a href="https://www.linkedin.com/company/litmus-automation/" >
    <img src="https://img.shields.io/badge/LinkedIn-Follow-0a66c2?style=flat-square" alt="Follow on LinkedIn" />
  </a>
</p>

# Litmus MCP Server

The official [Litmus Automation](https://litmus.io) **Model Context Protocol (MCP) Server** enables LLMs and intelligent systems to interact with [Litmus Edge](https://litmus.io/products/litmus-edge) for device configuration, monitoring, and management. It is built on top of the MCP SDK and adheres to the [Model Context Protocol spec](https://modelcontextprotocol.io/).

<div>
  <picture>
      <source media="(prefers-color-scheme: light)" srcset="static/MCP-server-arch-diagram.png" />
      <img src="static/MCP-server-arch-diagram.png" alt="Litmus MCP Server Architecture Diagram" />
  </picture>
</div>

## Table of Contents

- [Getting Started](#getting-started)
  - [Quick Launch (Docker)](#quick-launch-docker)
  - [Claude Code Setup](#claude-code-setup)
  - [Cursor IDE Setup](#cursor-ide-setup)
- [Tools](#available-tools)
- [Usage](#usage)
  - [Transport Modes](#transport-modes)
  - [Server-Sent Events (SSE)](#server-sent-events-sse)
  - [STDIO](#stdio)
- [Litmus Central](#litmus-central)
- [Integrations](#integrations)
  - [Cursor IDE](#cursor-ide)
  - [Claude Code](#claude-code)
  - [Claude Desktop](#claude-desktop)
  - [VS Code / Copilot](#vs-code--copilot)
  - [Windsurf](#windsurf)

---

## Getting Started

### Quick Launch (Docker)

Run the server in Docker:

```bash
docker run -d --name litmus-mcp-server -p 8000:8000 ghcr.io/litmusautomation/litmus-mcp-server:latest
```

The Litmus MCP Server is built for linux/AMD64 platforms. If running on an ARM64 OS, specify the AMD64 platform type by including the --platform argument:

```bash
docker run -d --name litmus-mcp-server --platform linux/amd64 -p 8000:8000 ghcr.io/litmusautomation/litmus-mcp-server:main
```

### Claude Code Setup
Example `./mcp.json` configuration:

```json
{
  "mcpServers": {
    "litmus-mcp-server": {
      "type": "sse",
      "url": "http://localhost:8000/sse",
      "headers": {
        "EDGE_URL": "${EDGE_URL}",
        "EDGE_API_CLIENT_ID": "${EDGE_API_CLIENT_ID}",
        "EDGE_API_CLIENT_SECRET": "${EDGE_API_CLIENT_SECRET}",
        "NATS_SOURCE": "${NATS_SOURCE}",
        "NATS_PORT": "${NATS_PORT:-4222}",
        "NATS_USER": "${NATS_USER}",
        "NATS_PASSWORD": "${NATS_PASSWORD}",
        "INFLUX_HOST": "${INFLUX_HOST}",
        "INFLUX_PORT": "${INFLUX_PORT:-8086}",
        "INFLUX_DB_NAME": "${INFLUX_DB_NAME:-tsdata}",
        "INFLUX_USERNAME": "${INFLUX_USERNAME}",
        "INFLUX_PASSWORD": "${INFLUX_PASSWORD}"
      }
    }
  }
}
```

### Cursor IDE Setup

Example `mcp.json` configuration:

```json
{
  "mcpServers": {
    "litmus-mcp-server": {
      "url": "http://<MCP_SERVER_IP>:8000/sse",
      "headers": {
        "EDGE_URL": "https://<LITMUSEDGE_IP>",
        "EDGE_API_CLIENT_ID": "<oauth2_client_id>",
        "EDGE_API_CLIENT_SECRET": "<oauth2_client_secret>",

        "NATS_SOURCE": "<LITMUSEDGE_IP>",
        "NATS_PORT": "4222",
        "NATS_USER": "<access_token_username>",
        "NATS_PASSWORD": "<access_token_from_litmusedge>",

        "INFLUX_HOST": "<LITMUSEDGE_IP>",
        "INFLUX_PORT": "8086",
        "INFLUX_DB_NAME": "tsdata",
        "INFLUX_USERNAME": "<datahub_username>",
        "INFLUX_PASSWORD": "<datahub_password>"
      }
    }
  }
}
```

**Header Configuration Guide:**
- `EDGE_URL`: Litmus Edge base URL (include https://)
- `EDGE_API_CLIENT_ID` / `EDGE_API_CLIENT_SECRET`: OAuth2 credentials from Litmus Edge
- `NATS_SOURCE`: Litmus Edge IP (no http/https)
- `NATS_USER` / `NATS_PASSWORD`: Access token credentials from **System → Access Control → Tokens**
- `INFLUX_HOST`: Litmus Edge IP (no http/https)
- `INFLUX_USERNAME` / `INFLUX_PASSWORD`: DataHub user credentials

See the [Cursor docs](https://docs.cursor.com/context/model-context-protocol) for more info.

---

## Available Tools

| Category                  | Function Name                         | Description |
|---------------------------|----------------------------------------|-------------|
| **DeviceHub**             | `get_litmusedge_driver_list`           | List supported Litmus Edge drivers (e.g., ModbusTCP, OPCUA, BACnet). |
|                           | `get_devicehub_devices`                | List all configured DeviceHub devices with connection settings and status. |
|                           | `create_devicehub_device`              | Create a new device with specified driver and default configuration. |
|                           | `get_devicehub_device_tags`            | Retrieve all tags (data points/registers) for a specific device. |
|                           | `get_current_value_of_devicehub_tag`   | Read the current real-time value of a specific device tag. |
| **Device Identity**       | `get_litmusedge_friendly_name`         | Get the human-readable name assigned to the Litmus Edge device. |
|                           | `set_litmusedge_friendly_name`         | Update the friendly name of the Litmus Edge device. |
| **LEM Integration**       | `get_cloud_activation_status`          | Check cloud registration and Litmus Edge Manager (LEM) connection status. |
| **Docker Management**     | `get_all_containers_on_litmusedge`     | List all Docker containers running on Litmus Edge Marketplace. |
|                           | `run_docker_container_on_litmusedge`   | Deploy and run a new Docker container on Litmus Edge Marketplace. |
| **NATS Topics** *         | `get_current_value_from_topic`         | Subscribe to a NATS topic and return the next published message. |
|                           | `get_multiple_values_from_topic`       | Collect multiple sequential values from a NATS topic for trend analysis. |
| **InfluxDB** **           | `get_historical_data_from_influxdb`    | Query historical time-series data from InfluxDB by measurement and time range. |
| **Digital Twins**         | `list_digital_twin_models`             | List all Digital Twin models with ID, name, description, and version. |
|                           | `list_digital_twin_instances`          | List all Digital Twin instances or filter by model ID. |
|                           | `create_digital_twin_instance`         | Create a new Digital Twin instance from an existing model. |
|                           | `list_static_attributes`               | List static attributes (fixed key-value pairs) for a model or instance. |
|                           | `list_dynamic_attributes`              | List dynamic attributes (real-time data points) for a model or instance. |
|                           | `list_transformations`                 | List data transformation rules configured for a Digital Twin model. |
|                           | `get_digital_twin_hierarchy`           | Get the hierarchy configuration for a Digital Twin model. |
|                           | `save_digital_twin_hierarchy`          | Save a new hierarchy configuration to a Digital Twin model. |

### Configuration Notes

**\* NATS Topic Tools Requirements:**
To use `get_current_value_from_topic` and `get_multiple_values_from_topic`, you must configure access control on Litmus Edge:
1. Navigate to: **Litmus Edge → System → Access Control → Tokens**
2. Create or configure an access token with appropriate permissions
3. Provide the token in your MCP client configuration headers

**\*\* InfluxDB Tools Requirements:**
To use `get_historical_data_from_influxdb`, you must allow InfluxDB port access:
1. Navigate to: **Litmus Edge → System → Network → Firewall**
2. Add a firewall rule to allow port **8086** on **TCP**
3. Ensure InfluxDB is accessible from the MCP server host

---

## Usage

### Transport Modes

The server supports two transport modes configured via `ENABLE_STDIO` in [src/config.py](src/config.py):

- **SSE Mode** (`ENABLE_STDIO = False`): HTTP-based transport for Cursor, VS Code, Claude Code, Windsurf
- **STDIO Mode** (`ENABLE_STDIO = True`): Process-based transport for Claude Desktop

### Server-Sent Events (SSE)

HTTP-based transport using [MCP SSE](https://modelcontextprotocol.io/docs/concepts/transports#server-sent-events-sse).

**Configuration:**
- Set `ENABLE_STDIO = False` in [src/config.py](src/config.py)
- Start server: `python3 src/server.py` or Docker
- Client endpoint: `http://<server-ip>:8000/sse`
- Authentication: HTTP headers

**Communication:**
- Server → Client: SSE stream
- Client → Server: HTTP POST

### STDIO

Process-based transport using stdin/stdout communication.

**Configuration:**
- Set `ENABLE_STDIO = True` in [src/config.py](src/config.py)
- Client spawns server process directly
- Authentication: Environment variables (`EDGE_API_CLIENT_ID`, `EDGE_API_CLIENT_SECRET`)

**Usage:**
```bash
EDGE_API_CLIENT_ID=<id> EDGE_API_CLIENT_SECRET=<secret> python3 src/server.py
```

---

## Litmus Central

Download or try Litmus Edge via [Litmus Central](https://central.litmus.io).

---

## Integrations

### Cursor IDE

Add to `~/.cursor/mcp.json` or `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "litmus-mcp-server": {
      "url": "http://<MCP_SERVER_IP>:8000/sse",
      "headers": {
        "EDGE_URL": "https://<LITMUSEDGE_IP>",
        "EDGE_API_CLIENT_ID": "<oauth2_client_id>",
        "EDGE_API_CLIENT_SECRET": "<oauth2_client_secret>",
        "NATS_SOURCE": "<LITMUSEDGE_IP>",
        "NATS_PORT": "4222",
        "NATS_USER": "<access_token_username>",
        "NATS_PASSWORD": "<access_token_from_litmusedge>",
        "INFLUX_HOST": "<LITMUSEDGE_IP>",
        "INFLUX_PORT": "8086",
        "INFLUX_DB_NAME": "tsdata",
        "INFLUX_USERNAME": "<datahub_username>",
        "INFLUX_PASSWORD": "<datahub_password>"
      }
    }
  }
}
```

[Cursor docs](https://docs.cursor.com/context/model-context-protocol)

---

### Claude Desktop

**Requirements:**
- Set `ENABLE_STDIO = True` in [src/config.py](src/config.py)
- Install Python dependencies: `uv sync` or `pip install -e .`

**Configuration:**

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "litmus-mcp-server": {
      "command": "python3",
      "args": [
        "/absolute/path/to/litmus-mcp-server/src/server.py"
      ],
      "env": {
        "PYTHONPATH": "/absolute/path/to/litmus-mcp-server/src",
        "EDGE_URL": "https://<LITMUSEDGE_IP>",
        "EDGE_API_CLIENT_ID": "<oauth2_client_id>",
        "EDGE_API_CLIENT_SECRET": "<oauth2_client_secret>",
        "NATS_SOURCE": "<LITMUSEDGE_IP>",
        "NATS_PORT": "4222",
        "NATS_USER": "<access_token_username>",
        "NATS_PASSWORD": "<access_token_from_litmusedge>",
        "INFLUX_HOST": "<LITMUSEDGE_IP>",
        "INFLUX_PORT": "8086",
        "INFLUX_DB_NAME": "tsdata",
        "INFLUX_USERNAME": "<datahub_username>",
        "INFLUX_PASSWORD": "<datahub_password>"
      }
    }
  }
}
```

See [claude_desktop_config.example.json](claude_desktop_config.example.json) for a complete template.

**Virtual Environment:**

For production use with virtual environment:

```json
{
  "mcpServers": {
    "litmus-mcp-server": {
      "command": "/absolute/path/to/litmus-mcp-server/.venv/bin/python",
      "args": ["/absolute/path/to/litmus-mcp-server/src/server.py"],
      "env": { /* same as above */ }
    }
  }
}
```

See [claude_desktop_config_venv.example.json](claude_desktop_config_venv.example.json) for the complete template.

[Anthropic Docs](https://docs.anthropic.com/en/docs/agents-and-tools/mcp)

---

### VS Code / GitHub Copilot

#### Manual Configuration

In VS Code:
Open User Settings (JSON) → Add:

```json
{
  "mcpServers": {
    "litmus-mcp-server": {
      "url": "http://<MCP_SERVER_IP>:8000/sse",
      "headers": {
        "EDGE_URL": "https://<LITMUSEDGE_IP>",
        "EDGE_API_CLIENT_ID": "<oauth2_client_id>",
        "EDGE_API_CLIENT_SECRET": "<oauth2_client_secret>",
        "NATS_SOURCE": "<LITMUSEDGE_IP>",
        "NATS_PORT": "4222",
        "NATS_USER": "<access_token_username>",
        "NATS_PASSWORD": "<access_token_from_litmusedge>",
        "INFLUX_HOST": "<LITMUSEDGE_IP>",
        "INFLUX_PORT": "8086",
        "INFLUX_DB_NAME": "tsdata",
        "INFLUX_USERNAME": "<datahub_username>",
        "INFLUX_PASSWORD": "<datahub_password>"
      }
    }
  }
}
```

Or use `.vscode/mcp.json` in your project.

[VS Code MCP Docs](https://code.visualstudio.com/docs/copilot/chat/mcp-servers)

---

### Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "litmus-mcp-server": {
      "url": "http://<MCP_SERVER_IP>:8000/sse",
      "headers": {
        "EDGE_URL": "https://<LITMUSEDGE_IP>",
        "EDGE_API_CLIENT_ID": "<oauth2_client_id>",
        "EDGE_API_CLIENT_SECRET": "<oauth2_client_secret>",
        "NATS_SOURCE": "<LITMUSEDGE_IP>",
        "NATS_PORT": "4222",
        "NATS_USER": "<access_token_username>",
        "NATS_PASSWORD": "<access_token_from_litmusedge>",
        "INFLUX_HOST": "<LITMUSEDGE_IP>",
        "INFLUX_PORT": "8086",
        "INFLUX_DB_NAME": "tsdata",
        "INFLUX_USERNAME": "<datahub_username>",
        "INFLUX_PASSWORD": "<datahub_password>"
      }
    }
  }
}
```

[Windsurf MCP Docs](https://docs.windsurf.com/windsurf/mcp)

### MCP server registries

- [Glama](https://glama.ai/mcp/servers/@litmusautomation/litmus-mcp-server)

 <a href="https://glama.ai/mcp/servers/@litmusautomation/litmus-mcp-server">
 <img width="380" height="200" src="https://glama.ai/mcp/servers/@litmusautomation/litmus-mcp-server/badge" alt="Litmus MCP server" />
 </a>

- [MCP.so](https://mcp.so/server/litmus-mcp-server/litmusautomation)

---

© 2025 Litmus Automation, Inc. All rights reserved.
