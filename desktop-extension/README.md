# Litmus MCP Desktop Extension

One-click Claude Desktop access to Litmus Edge. This extension bridges
Claude Desktop (stdio) to a [Litmus MCP Server](https://github.com/litmusautomation/litmus-mcp-server)
running on your network, forwarding your connection settings as
per-request headers. Credential fields are stored in the operating
system keychain by Claude Desktop and are sent only to the server URL
you configure.

## Prerequisites

- A running Litmus MCP Server ([Quick Launch](../README.md#quick-launch)),
  reachable from this machine, e.g. `https://mcp.example.com` (see
  [HTTPS Deployment](../README.md#https-deployment)) or
  `http://localhost:8000`.
- Litmus Edge OAuth2 API credentials (System > API Access on the device).
- Optional: NATS and InfluxDB credentials for the real-time and
  historical data tools.

## Install

Download `litmus-mcp.mcpb` from the releases page and open it with
Claude Desktop (or double-click it). Fill in the configuration dialog
and start chatting.

## Build from source

```bash
cd desktop-extension
npm install --omit=dev
npx @anthropic-ai/mcpb pack . litmus-mcp.mcpb
```

## Privacy Policy

This extension does not collect, store, or transmit any data to Litmus
Automation or third parties. All traffic flows directly from Claude
Desktop to the Litmus MCP Server URL you configure, carrying the
credentials you entered; those credentials are stored locally by Claude
Desktop in the operating system keychain. Data handling by Litmus
products is described in the Litmus privacy policy:
https://litmus.io/privacy-policy

## License

Apache-2.0, same as the parent repository.
