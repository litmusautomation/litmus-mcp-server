#!/usr/bin/env node
/**
 * Bridges Claude Desktop (stdio) to a Litmus MCP Server (streamable HTTP),
 * forwarding the connection settings from the extension's user config as
 * per-request headers. The heavy lifting is done by mcp-remote; this
 * launcher only validates the configuration and assembles its arguments so
 * that unset optional settings produce no header at all.
 *
 * mcp-remote is loaded into this process rather than spawned. Claude Desktop
 * runs extensions on its embedded Node, so process.execPath is the Claude
 * Desktop executable and not a node binary, and that executable ships with
 * Electron's runAsNode fuse disabled: exec'ing it with a script path starts a
 * second copy of the desktop app instead of the bridge, which then never
 * answers initialize.
 *
 * Credentials are forwarded as plaintext headers, so plain HTTP is only
 * permitted to loopback addresses unless the user explicitly opts in.
 */
const path = require("path");
const { pathToFileURL } = require("url");

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
  // Litmus Unify authenticates separately from Litmus Edge. Without these the
  // server hides the unify.* namespace from discovery rather than advertising
  // functions that cannot authenticate.
  "UNS_URL",
  "UNS_USERNAME",
  "UNS_PASSWORD",
  "VALIDATE_CERTIFICATE",
];

// Values that must never reach stderr. mcp-remote logs the whole header set
// verbatim at startup, and Claude Desktop persists an extension's stderr to a
// per-server log file that users routinely copy into bug reports, so the
// secrets would otherwise sit on disk in plaintext.
const SECRET_VARS = [
  "EDGE_API_CLIENT_SECRET",
  "NATS_PASSWORD",
  "INFLUX_PASSWORD",
  "UNS_PASSWORD",
];

const REDACTED = "[redacted]";

// Settings that are only meaningful together: configuring one without the
// others yields a connection error from the SDK naming internal option names
// rather than the fields the dialog shows, so they are checked here instead.
const GROUPED_VARS = [
  {
    label: "Litmus Unify",
    names: [
      ["UNS_URL", "Litmus Unify URL"],
      ["UNS_USERNAME", "Litmus Unify Username"],
      ["UNS_PASSWORD", "Litmus Unify Password"],
    ],
  },
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

  for (const { label, names } of GROUPED_VARS) {
    const set = names.filter(([name]) => readVar(env, name));
    if (set.length && set.length !== names.length) {
      const absent = names
        .filter(([name]) => !readVar(env, name))
        .map(([, fieldLabel]) => fieldLabel);
      throw new ConfigError(
        `${label} is partly configured: ${absent.join(", ")} ` +
          `${absent.length > 1 ? "are" : "is"} still empty. Fill ${
            absent.length > 1 ? "them" : "it"
          } in, or clear the ${label} settings entirely to skip it.`
      );
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

/**
 * Masks every configured secret in anything written to stderr, for the whole
 * life of the process. Only stderr is filtered: stdout carries the JSON-RPC
 * stream, which never contains these values and must not be rewritten.
 *
 * Both the raw value and its JSON-escaped form are matched, since the header
 * dump that prompted this goes through JSON.stringify. Any non-empty secret is
 * masked regardless of length; over-masking a diagnostic line is harmless,
 * missing one is not.
 *
 * @returns {() => void} restores the original stderr, for tests.
 */
function redactSecrets(env, stream = process.stderr) {
  const needles = [];
  for (const name of SECRET_VARS) {
    const value = readVar(env, name);
    if (!value) {
      continue;
    }
    needles.push(value);
    const escaped = JSON.stringify(value).slice(1, -1);
    if (escaped !== value) {
      needles.push(escaped);
    }
  }
  if (!needles.length) {
    return () => {};
  }
  // Longest first, so a secret that contains a shorter one is masked whole
  // rather than being left with a readable tail.
  needles.sort((a, b) => b.length - a.length);

  const original = stream.write.bind(stream);
  stream.write = (chunk, encoding, callback) => {
    if (typeof encoding === "function") {
      callback = encoding;
      encoding = undefined;
    }
    const text =
      typeof chunk === "string"
        ? chunk
        : Buffer.isBuffer(chunk)
          ? chunk.toString("utf8")
          : null;
    if (text === null || !needles.some((needle) => text.includes(needle))) {
      return original(chunk, encoding, callback);
    }
    let masked = text;
    for (const needle of needles) {
      masked = masked.split(needle).join(REDACTED);
    }
    return original(masked, encoding, callback);
  };
  return () => {
    stream.write = original;
  };
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

async function main() {
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

  let entry;
  try {
    entry = resolveMcpRemote();
  } catch (err) {
    console.error(
      `Litmus MCP: failed to start the bridge: ${err.message}. Reinstall the ` +
        `extension if this persists.`
    );
    process.exit(1);
  }

  // Installed before the bridge loads, so its startup header dump is masked.
  redactSecrets(process.env);

  // mcp-remote reads its configuration from process.argv and starts on import,
  // so argv has to look like the bin invocation it expects: argv[2] onward.
  process.argv = [process.argv[0], entry, plan.url, ...plan.args];
  try {
    // ESM package, so a dynamic import from this CommonJS launcher. The file
    // URL matters on Windows, where import() rejects a bare drive path.
    await import(pathToFileURL(entry).href);
  } catch (err) {
    console.error(`Litmus MCP: failed to start the bridge: ${err.message}`);
    process.exit(1);
  }
}

if (require.main === module) {
  main().catch((err) => {
    console.error(`Litmus MCP: failed to start the bridge: ${err.message}`);
    process.exit(1);
  });
}

module.exports = {
  buildLaunch,
  resolveMcpRemote,
  redactSecrets,
  ConfigError,
  HEADER_VARS,
  SECRET_VARS,
  REDACTED,
};
