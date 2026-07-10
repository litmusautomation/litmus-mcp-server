#!/usr/bin/env node
/**
 * Bridges Claude Desktop (stdio) to a Litmus MCP Server (streamable HTTP),
 * forwarding the connection settings from the extension's user config as
 * per-request headers. The heavy lifting is done by mcp-remote; this
 * launcher only assembles its arguments from the environment so that
 * unset optional settings produce no header at all.
 */
const { spawn } = require("child_process");
const path = require("path");

const HEADER_VARS = [
  "EDGE_URL",
  "EDGE_API_CLIENT_ID",
  "EDGE_API_CLIENT_SECRET",
  "NATS_SOURCE",
  "NATS_PORT",
  "NATS_USER",
  "NATS_PASSWORD",
  "INFLUX_HOST",
  "INFLUX_PORT",
  "INFLUX_DB_NAME",
  "INFLUX_USERNAME",
  "INFLUX_PASSWORD",
];

const base = (process.env.LITMUS_MCP_SERVER_URL || "").trim().replace(/\/+$/, "");
if (!base) {
  console.error("Litmus MCP: 'Litmus MCP Server URL' is not configured.");
  process.exit(1);
}
const url = /\/(mcp|sse)$/.test(base) ? base : `${base}/mcp`;

const pkg = require("mcp-remote/package.json");
const bin = path.join(
  path.dirname(require.resolve("mcp-remote/package.json")),
  typeof pkg.bin === "string" ? pkg.bin : pkg.bin["mcp-remote"]
);

const args = [bin, url];
if (url.startsWith("http://")) {
  // The user explicitly configured a plain-HTTP URL (e.g. localhost or a
  // trusted plant network); mcp-remote refuses non-localhost http without
  // this flag.
  args.push("--allow-http");
}
for (const name of HEADER_VARS) {
  const value = (process.env[name] || "").trim();
  if (value) {
    args.push("--header", `${name}:${value}`);
  }
}

const child = spawn(process.execPath, args, { stdio: "inherit" });
child.on("exit", (code) => process.exit(code ?? 1));
child.on("error", (err) => {
  console.error(`Litmus MCP: failed to start bridge: ${err.message}`);
  process.exit(1);
});
