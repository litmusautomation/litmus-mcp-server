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

`Litmus MCP Server URL`, `Litmus Edge URL`, `Edge OAuth2 Client ID` and
`Edge OAuth2 Client Secret` are required; the launcher checks all four
before connecting and names any that are missing. The NATS and InfluxDB
fields are optional and only enable the real-time and historical data
tools.

## Transport security

Your configuration is forwarded to the Litmus MCP Server as request
headers, which means the Edge client secret and any NATS or InfluxDB
passwords travel with every request. To keep them off the wire in
cleartext:

- `https://` URLs are always accepted. This is the recommended setup;
  see [HTTPS Deployment](../README.md#https-deployment).
- `http://` URLs are accepted for loopback hosts only (`localhost`,
  `127.0.0.0/8`, `::1`), where the traffic never leaves the machine.
- `http://` URLs pointing anywhere else are refused, and the extension
  will not start until you switch to `https://`. If the network is
  genuinely trusted, you can enable `Allow insecure HTTP` in the
  extension's configuration; the launcher then connects but logs a
  warning on every start.

## Build from source

```bash
cd desktop-extension
npm install --omit=dev
npm test
npx @anthropic-ai/mcpb pack . litmus-mcp.mcpb
```

`npm test` runs the launcher's configuration and transport-security
tests with the built-in Node test runner and needs no dev dependencies.

## Privacy Policy

This extension does not collect, store, or transmit any data to Litmus
Automation or third parties. All traffic flows directly from Claude
Desktop to the Litmus MCP Server URL you configure, carrying the
credentials you entered; those credentials are stored locally by Claude
Desktop in the operating system keychain. Data handling by Litmus
products is described in the Litmus privacy policy:
https://litmus.io/privacy-policy

## License

MIT, same as the parent repository. The full text ships in the bundle as
`LICENSE`.
