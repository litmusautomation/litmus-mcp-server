#!/usr/bin/env node
/**
 * Bridges Claude Desktop (stdio) to a Litmus MCP Server (streamable HTTP),
 * forwarding the connection settings from the extension's user config as
 * per-request headers. The heavy lifting is done by mcp-remote; this
 * launcher only validates the configuration and assembles its arguments so
 * that unset optional settings produce no header at all.
 *
 * Credentials are forwarded as plaintext headers, so plain HTTP is only
 * permitted to loopback addresses unless the user explicitly opts in.
 */
const { spawn } = require("child_process");
const path = require("path");

const HEADER_VARS = [
  "EDGE_URL",
  "EDGE_API_CLIENT_ID",
  "EDGE_API_CLIENT_SECRET",
  "NATS_SOURCE",
  "NATS_PORT",
  "NATS_PASSWORD",
  "INFLUX_HOST",
  "INFLUX_PORT",
  "INFLUX_DB_NAME",
  "INFLUX_USERNAME",
  "INFLUX_PASSWORD",
  "VALIDATE_CERTIFICATE",
];

// Fields the server cannot authenticate without, with the labels the
// configuration dialog shows so the error names what the user has to fix.
const REQUIRED_VARS = [
  ["LITMUS_MCP_SERVER_URL", "Litmus MCP Server URL"],
  ["EDGE_URL", "Litmus Edge URL"],
  ["EDGE_API_CLIENT_ID", "Edge OAuth2 Client ID"],
  ["EDGE_API_CLIENT_SECRET", "Edge OAuth2 Client Secret"],
];

const PORT_VARS = [
  ["NATS_PORT", "NATS Port"],
  ["INFLUX_PORT", "InfluxDB Port"],
];

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

/** Configuration the user can correct, as opposed to an internal failure. */
class ConfigError extends Error {}

/**
 * Claude Desktop substitutes an empty string for blank optional fields, but
 * an unsubstituted `${user_config.x}` placeholder can also reach us; treat
 * both as unset rather than forwarding a literal placeholder as a header.
 */
function readVar(env, name) {
  const value = (env[name] || "").trim();
  return /^\$\{.*\}$/.test(value) ? "" : value;
}

function isTruthy(value) {
  return /^(true|1|yes|on)$/i.test(value);
}

function isLoopbackHost(hostname) {
  const host = hostname.toLowerCase();
  if (LOOPBACK_HOSTS.has(host)) {
    return true;
  }
  // The whole 127.0.0.0/8 block is loopback, not just 127.0.0.1.
  return /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(host);
}

function parseUrl(raw, label) {
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new ConfigError(
      `'${label}' is not a valid URL: ${raw}. Include the scheme, e.g. https://mcp.example.com`
    );
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new ConfigError(
      `'${label}' must use http:// or https://, got ${parsed.protocol}//`
    );
  }
  return parsed;
}

/**
 * Validates the whole configuration up front and returns the mcp-remote
 * argument list plus any warnings worth surfacing to the user.
 *
 * @throws {ConfigError} when the user has to change a setting.
 */
function buildLaunch(env) {
  const missing = REQUIRED_VARS.filter(([name]) => !readVar(env, name)).map(
    ([, label]) => label
  );
  if (missing.length) {
    throw new ConfigError(
      `required setting${missing.length > 1 ? "s" : ""} not configured: ` +
        `${missing.join(", ")}. Open the extension's configuration in Claude ` +
        `Desktop (Settings > Extensions > Litmus MCP) and fill ${
          missing.length > 1 ? "them" : "it"
        } in.`
    );
  }

  const base = readVar(env, "LITMUS_MCP_SERVER_URL").replace(/\/+$/, "");
  const serverUrl = parseUrl(base, "Litmus MCP Server URL");
  parseUrl(readVar(env, "EDGE_URL"), "Litmus Edge URL");

  for (const [name, label] of PORT_VARS) {
    const value = readVar(env, name);
    if (!value) {
      continue;
    }
    const port = Number(value);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new ConfigError(`'${label}' must be a port number, got ${value}`);
    }
  }

  const warnings = [];
  // Flags and headers only; the caller prepends the bin and url.
  const args = [];
  const url = /\/(mcp|sse)$/.test(base) ? base : `${base}/mcp`;

  if (serverUrl.protocol === "http:") {
    const loopback = isLoopbackHost(serverUrl.hostname);
    if (!loopback && !isTruthy(readVar(env, "LITMUS_ALLOW_INSECURE_HTTP"))) {
      throw new ConfigError(
        `refusing to send credentials in cleartext to ${serverUrl.host}. ` +
          `'Litmus MCP Server URL' uses http:// with a non-loopback host, so ` +
          `the Edge client secret and any NATS/InfluxDB passwords would cross ` +
          `the network unencrypted. Use https:// instead (see the HTTPS ` +
          `Deployment section of the Litmus MCP Server README), or enable ` +
          `'Allow insecure HTTP' in the extension's configuration if this ` +
          `network is trusted.`
      );
    }
    if (!loopback) {
      warnings.push(
        `sending credentials UNENCRYPTED to ${serverUrl.host} because ` +
          `'Allow insecure HTTP' is enabled. Anyone on the network path can ` +
          `read the Edge client secret and any NATS/InfluxDB passwords. ` +
          `Switch the server to https:// when you can.`
      );
    }
    // mcp-remote rejects http:// targets unless this is passed.
    args.push("--allow-http");
  }

  for (const name of HEADER_VARS) {
    const value = readVar(env, name);
    if (value) {
      args.push("--header", `${name}:${value}`);
    }
  }

  return { url, args, warnings };
}

/** Locates the mcp-remote entry point bundled alongside this launcher. */
function resolveMcpRemote() {
  const manifestPath = require.resolve("mcp-remote/package.json");
  const pkg = require(manifestPath);
  const bin = typeof pkg.bin === "string" ? pkg.bin : pkg.bin && pkg.bin["mcp-remote"];
  if (!bin) {
    throw new Error("mcp-remote/package.json declares no 'mcp-remote' bin");
  }
  return path.join(path.dirname(manifestPath), bin);
}

function main() {
  let plan;
  try {
    plan = buildLaunch(process.env);
  } catch (err) {
    if (err instanceof ConfigError) {
      console.error(`Litmus MCP: ${err.message}`);
    } else {
      console.error(`Litmus MCP: could not read configuration: ${err.message}`);
    }
    process.exit(1);
  }

  for (const warning of plan.warnings) {
    console.error(`Litmus MCP: WARNING: ${warning}`);
  }

  let child;
  try {
    child = spawn(process.execPath, [resolveMcpRemote(), plan.url, ...plan.args], {
      stdio: "inherit",
    });
  } catch (err) {
    console.error(
      `Litmus MCP: failed to start the bridge: ${err.message}. Reinstall the ` +
        `extension if this persists.`
    );
    process.exit(1);
  }

  child.on("exit", (code) => process.exit(code ?? 1));
  child.on("error", (err) => {
    console.error(`Litmus MCP: failed to start the bridge: ${err.message}`);
    process.exit(1);
  });
}

if (require.main === module) {
  main();
}

module.exports = { buildLaunch, resolveMcpRemote, ConfigError, HEADER_VARS };
