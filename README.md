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
  - [Cursor IDE Setup](#cursor-ide-setup)
- [API](#api)
- [Usage](#usage)
  - [Server-Sent Events (SSE)](#server-sent-events-sse)
- [Litmus Central](#litmus-central)
- [Integrations](#integrations)
  - [Cursor IDE](#cursor-ide)
  - [Claude Desktop](#claude-desktop)
  - [VS Code / Copilot](#vs-code--copilot)
  - [Windsurf](#windsurf)

---

## Getting Started

### Quick Launch (Docker)

Run the server in Docker:

```bash
docker run -d --name litmus-mcp-server -p 8000:8000 ghcr.io/litmusautomation/litmus-mcp-server:main
```

### Cursor IDE Setup

Example `mcp.json` configuration:

```json
{
  "mcpServers": {
    "litmus-mcp-server": {
      "url": "http://<IP Address>:8000/sse"
    }
  }
}
```

See the [Cursor docs](https://docs.cursor.com/context/model-context-protocol) for more info.

---

## API

| Category                  | Function Name                         | Description |
|---------------------------|----------------------------------------|-------------|
| **Edge System Config**    | `get_current_environment_config`       | Get current environment configuration used for Litmus Edge connectivity. |
|                           | `update_environment_config`            | Update environment variable config for connecting to Litmus Edge. |
|                           | `get_current_config`                   | Retrieve current Litmus Edge instance configuration. |
|                           | `update_config`                        | Update configuration of the device or container running Litmus Edge. |
| **DeviceHub**             | `get_litmusedge_driver_list`           | List supported Litmus Edge drivers. |
|                           | `get_devicehub_devices`                | List devices configured in DeviceHub. |
|                           | `get_devicehub_device_tags`           | Retrieve tags for a specific DeviceHub device. |
|                           | `get_current_value_of_devicehub_tag`   | Get current value of a specific device tag. |
|                           | `create_devicehub_device`              | Register a new DeviceHub device. Supports various protocols and templates for register-based data polling. |
| **Device Identity**       | `get_litmusedge_friendly_name`         | Retrieve the user-friendly name of the device. |
|                           | `set_litmusedge_friendly_name`         | Assign or update the friendly name. |
| **LEM Integration**       | `get_cloud_activation_status`          | Check cloud activation and Litmus Edge Manager (LEM) connection status. |
| **Docker Management**     | `get_all_containers_on_litmusedge`     | List all containers on Litmus Edge. |
|                           | `run_docker_container_on_litmusedge`   | Launch a Docker container via Litmus Edge Marketplace (not the MCP host). |
| **Topic Subscription**    | `get_current_value_on_topic`           | Subscribe to current values on a Litmus Edge topic. Use global `NATS_STATUS = False` to unsubscribe. |
|                           | `get_multiple_values_from_topic`       | Retrieve multiple values from a topic for plotting or batch access. |

---

## Usage

### Server-Sent Events (SSE)

This server supports the [MCP SSE transport](https://modelcontextprotocol.io/docs/concepts/transports#server-sent-events-sse) for real-time communication.

- **Client endpoint:** `http://<server-ip>:8000/sse`
- **Default binding:** `0.0.0.0:8000/sse`
- **Communication:**
  - Server → Client: Streamed via SSE
  - Client → Server: HTTP POST

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
      "url": "http://<IP Address>:8000/sse"
    }
  }
}
```

[Cursor docs](https://docs.cursor.com/context/model-context-protocol)

---

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "litmus-mcp-server": {
      "url": "http://<IP Address>:8000/sse"
    }
  }
}
```

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
      "url": "http://<IP Address>:8000/sse"
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
      "url": "http://<IP Address>:8000/sse"
    }
  }
}
```

[Windsurf MCP Docs](https://docs.windsurf.com/windsurf/mcp)

---

© 2025 Litmus Automation, Inc. All rights reserved.
