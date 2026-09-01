const test = require("node:test");
const assert = require("node:assert/strict");

const { buildLaunch, resolveMcpRemote, ConfigError } = require("../server/index.js");

const VALID = {
  LITMUS_MCP_SERVER_URL: "https://mcp.example.com",
  EDGE_URL: "https://192.168.1.50",
  EDGE_API_CLIENT_ID: "client-id",
  EDGE_API_CLIENT_SECRET: "client-secret",
};

function env(overrides) {
  return { ...VALID, ...overrides };
}

/** Asserts fn() raises a ConfigError and returns it, so callers can inspect it. */
function configError(fn, what) {
  try {
    fn();
  } catch (err) {
    assert.ok(err instanceof ConfigError, `${what}: expected ConfigError, got ${err}`);
    return err;
  }
  assert.fail(`${what}: expected a ConfigError, none thrown`);
}

function headers(args) {
  const out = {};
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--header") {
      const [name, ...rest] = args[i + 1].split(":");
      out[name] = rest.join(":");
    }
  }
  return out;
}

test("appends /mcp and forwards the required headers", () => {
  const { url, args, warnings } = buildLaunch(env({}));
  assert.equal(url, "https://mcp.example.com/mcp");
  assert.deepEqual(warnings, []);
  assert.ok(!args.includes("--allow-http"));
  assert.deepEqual(headers(args), {
    EDGE_URL: "https://192.168.1.50",
    EDGE_API_CLIENT_ID: "client-id",
    EDGE_API_CLIENT_SECRET: "client-secret",
  });
});

test("keeps an explicit /mcp or /sse suffix and strips trailing slashes", () => {
  assert.equal(
    buildLaunch(env({ LITMUS_MCP_SERVER_URL: "https://mcp.example.com/sse" })).url,
    "https://mcp.example.com/sse"
  );
  assert.equal(
    buildLaunch(env({ LITMUS_MCP_SERVER_URL: "https://mcp.example.com/mcp//" })).url,
    "https://mcp.example.com/mcp"
  );
});

test("names every missing required field at startup", () => {
  const err = configError(
    () =>
      buildLaunch({
        LITMUS_MCP_SERVER_URL: "https://mcp.example.com",
        EDGE_API_CLIENT_ID: "",
      }),
    "missing required fields"
  );
  assert.match(err.message, /Litmus Edge URL/);
  assert.match(err.message, /Edge OAuth2 Client ID/);
  assert.match(err.message, /Edge OAuth2 Client Secret/);
});

test("treats unsubstituted user_config placeholders as unset", () => {
  const err = configError(
    () => buildLaunch(env({ EDGE_API_CLIENT_SECRET: "${user_config.edge_api_client_secret}" })),
    "placeholder secret"
  );
  assert.match(err.message, /Edge OAuth2 Client Secret/);

  const { args } = buildLaunch(env({ NATS_SOURCE: "${user_config.nats_source}" }));
  assert.equal(headers(args).NATS_SOURCE, undefined);
});

test("rejects a malformed or non-http server URL", () => {
  for (const value of ["mcp.example.com", "not a url", "ftp://mcp.example.com"]) {
    assert.throws(() => buildLaunch(env({ LITMUS_MCP_SERVER_URL: value })), ConfigError);
  }
});

test("rejects a malformed Edge URL", () => {
  assert.throws(() => buildLaunch(env({ EDGE_URL: "192.168.1.50" })), ConfigError);
});

test("rejects a non-numeric or out-of-range port", () => {
  for (const value of ["not-a-port", "0", "70000", "4222.5"]) {
    assert.throws(() => buildLaunch(env({ NATS_PORT: value })), ConfigError);
    assert.throws(() => buildLaunch(env({ INFLUX_PORT: value })), ConfigError);
  }
  assert.ok(buildLaunch(env({ NATS_PORT: "4222", INFLUX_PORT: "8086" })));
});

test("allows plain HTTP to loopback hosts", () => {
  for (const host of [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://127.1.2.3:8000",
    "http://[::1]:8000",
    "http://LOCALHOST:8000",
  ]) {
    const { args, warnings } = buildLaunch(env({ LITMUS_MCP_SERVER_URL: host }));
    assert.ok(args.includes("--allow-http"), `${host} should pass --allow-http`);
    assert.deepEqual(warnings, [], `${host} should not warn`);
  }
});

test("refuses plain HTTP to a routable host and forwards nothing", () => {
  for (const host of ["http://192.168.1.50:8000", "http://mcp.example.com", "http://10.0.0.5"]) {
    const err = configError(
      () => buildLaunch(env({ LITMUS_MCP_SERVER_URL: host })),
      `${host} should be refused`
    );
    assert.match(err.message, /cleartext/);
    assert.match(err.message, /https:\/\//);
    // The refusal must happen before any credential is put on an argv.
    assert.doesNotMatch(err.message, /client-secret/);
  }
});

test("permits routable plain HTTP only with the opt-in, and warns loudly", () => {
  const { args, warnings } = buildLaunch(
    env({
      LITMUS_MCP_SERVER_URL: "http://192.168.1.50:8000",
      LITMUS_ALLOW_INSECURE_HTTP: "true",
    })
  );
  assert.ok(args.includes("--allow-http"));
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /UNENCRYPTED/);
  assert.match(warnings[0], /192\.168\.1\.50:8000/);
  assert.equal(headers(args).EDGE_API_CLIENT_SECRET, "client-secret");
});

test("does not treat a false-y or unsubstituted opt-in as consent", () => {
  for (const value of ["false", "", "${user_config.allow_insecure_http}", "no"]) {
    assert.throws(
      () =>
        buildLaunch(
          env({
            LITMUS_MCP_SERVER_URL: "http://192.168.1.50:8000",
            LITMUS_ALLOW_INSECURE_HTTP: value,
          })
        ),
      ConfigError,
      `opt-in value ${JSON.stringify(value)} should not permit cleartext`
    );
  }
});

test("the opt-in does not weaken anything over https", () => {
  const { args, warnings } = buildLaunch(env({ LITMUS_ALLOW_INSECURE_HTTP: "true" }));
  assert.ok(!args.includes("--allow-http"));
  assert.deepEqual(warnings, []);
});

test("resolves the bundled mcp-remote entry point", () => {
  assert.match(resolveMcpRemote(), /mcp-remote/);
});

// ── Litmus Unify ─────────────────────────────────────────────────────────────
//
// Unify authenticates separately from Litmus Edge, so its settings are optional
// but only meaningful as a set. Without them the server hides the unify.*
// namespace from discovery instead of advertising functions that cannot
// authenticate, so forwarding all three is what makes the namespace reachable.

const UNIFY = {
  UNS_URL: "https://unify.example.com",
  UNS_USERNAME: "uns-user",
  UNS_PASSWORD: "uns-pass",
};

test("forwards the Unify headers when all three are set", () => {
  const sent = headers(buildLaunch(env(UNIFY)).args);
  assert.equal(sent.UNS_URL, "https://unify.example.com");
  assert.equal(sent.UNS_USERNAME, "uns-user");
  assert.equal(sent.UNS_PASSWORD, "uns-pass");
});

test("sends no Unify headers when none are set", () => {
  const sent = headers(buildLaunch(env({})).args);
  for (const name of Object.keys(UNIFY)) {
    assert.equal(name in sent, false, `${name} should not be sent`);
  }
});

test("a partly configured Unify names the empty fields", () => {
  const err = configError(
    () => buildLaunch(env({ UNS_URL: UNIFY.UNS_URL })),
    "url only"
  );
  assert.match(err.message, /Litmus Unify is partly configured/);
  assert.match(err.message, /Litmus Unify Username/);
  assert.match(err.message, /Litmus Unify Password/);
  // Clearing the group has to be offered as the alternative to filling it in.
  assert.match(err.message, /clear the Litmus Unify settings entirely/);
});

test("a partly configured Unify is singular about one empty field", () => {
  const err = configError(
    () => buildLaunch(env({ UNS_URL: UNIFY.UNS_URL, UNS_USERNAME: "u" })),
    "password missing"
  );
  assert.match(err.message, /Litmus Unify Password is still empty/);
});

test("unsubstituted Unify placeholders count as unset, not as partial config", () => {
  const { args } = buildLaunch(
    env({
      UNS_URL: "${user_config.uns_url}",
      UNS_USERNAME: "${user_config.uns_username}",
      UNS_PASSWORD: "${user_config.uns_password}",
    })
  );
  assert.equal("UNS_URL" in headers(args), false);
});

// Claude Desktop runs extensions on its embedded Node, so process.execPath is
// the Claude Desktop executable, and that executable ships with Electron's
// runAsNode fuse disabled. Exec'ing it with a script path launched a second
// copy of the desktop app instead of the bridge, so initialize went
// unanswered and no mcp-remote output ever reached the log. mcp-remote has to
// stay in this process.
test("the launcher starts no child process", () => {
  const source = require("node:fs").readFileSync(
    require("node:path").join(__dirname, "..", "server", "index.js"),
    "utf8"
  );
  const code = source.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "");
  assert.equal(/child_process|\bspawn\s*\(|\bfork\s*\(|execPath/.test(code), false);
});

const { redactSecrets, SECRET_VARS, REDACTED } = require("../server/index.js");

/** Captures what a redacted stream actually emits. */
function capture(overrides) {
  const written = [];
  const stream = { write: (chunk) => written.push(String(chunk)) };
  const restore = redactSecrets(env(overrides), stream);
  return { stream, written, restore };
}

test("masks the startup header dump that mcp-remote writes to stderr", () => {
  const { stream, written } = capture({
    NATS_PASSWORD: "nats-pw",
    INFLUX_PASSWORD: "influx-pw",
  });
  stream.write(
    `Using custom headers: ${JSON.stringify({
      EDGE_API_CLIENT_ID: "client-id",
      EDGE_API_CLIENT_SECRET: "client-secret",
      NATS_PASSWORD: "nats-pw",
      INFLUX_PASSWORD: "influx-pw",
    })}\n`
  );
  const out = written.join("");
  for (const secret of ["client-secret", "nats-pw", "influx-pw"]) {
    assert.equal(out.includes(secret), false, `${secret} reached stderr`);
  }
  // Non-secret context stays readable, or the logs stop being useful.
  assert.match(out, /Using custom headers/);
  assert.match(out, /client-id/);
  assert.match(out, new RegExp(REDACTED.replace(/[[\]]/g, "\\$&")));
});

test("masks every declared secret, not just the Edge client secret", () => {
  for (const name of SECRET_VARS) {
    const { stream, written } = capture({ [name]: `value-of-${name}` });
    stream.write(`leaked ${`value-of-${name}`}\n`);
    assert.equal(written.join("").includes(`value-of-${name}`), false, name);
  }
});

test("masks a secret that arrives JSON-escaped", () => {
  const secret = 'quote"and\\slash';
  const { stream, written } = capture({ EDGE_API_CLIENT_SECRET: secret });
  stream.write(`${JSON.stringify({ EDGE_API_CLIENT_SECRET: secret })}\n`);
  const out = written.join("");
  assert.equal(out.includes(JSON.stringify(secret).slice(1, -1)), false);
  assert.equal(out.includes(secret), false);
});

test("masks a secret split across a Buffer write", () => {
  const { stream, written } = capture({ EDGE_API_CLIENT_SECRET: "client-secret" });
  stream.write(Buffer.from("token=client-secret\n", "utf8"));
  assert.equal(written.join("").includes("client-secret"), false);
});

test("leaves output alone when no secret is configured", () => {
  const { stream, written, restore } = capture({});
  stream.write("Connecting to remote server\n");
  assert.equal(written.join(""), "Connecting to remote server\n");
  restore();
});

test("masks the longer secret whole when one contains another", () => {
  const { stream, written } = capture({
    EDGE_API_CLIENT_SECRET: "abcd1234",
    NATS_PASSWORD: "abcd",
  });
  const out = (stream.write("secret=abcd1234\n"), written.join(""));
  assert.equal(out.includes("abcd1234"), false);
  assert.equal(out.includes("1234"), false, "left a readable tail");
});

test("TLS verification is on out of the box", () => {
  const manifest = require("../manifest.json");
  assert.equal(manifest.user_config.validate_certificate.default, true);
});
