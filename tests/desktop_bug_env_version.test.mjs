// The bug report always said "Hermes version: unknown": it read host.appVersion, which the
// Desktop SDK does not have. The SDK's host.status() carries the version. hidePane is not an
// SDK member either; the call to it was dead code.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");
const start = src.indexOf("async function collectPluginBugEnv() {");
const end = src.indexOf("\nasync function recoverPlayer()");
const body = src.slice(start, end);

function collect(host) {
  const atom = (v) => ({ get: () => v });
  return new Function("host", "bridge", "rest", "$live", "ctxRef", "navigator",
    "pluginPlayerPlaceLabel", "formatDiagnostics",
    `${body}\nreturn collectPluginBugEnv();`)(
    host, null, async () => ({ version: "1.0.0", build: "abc" }), atom(null), null,
    { platform: "Linux", userAgent: "test" }, () => "Frame", () => "");
}

// Shaped like apps/desktop/src/sdk/index.ts: status() is async, there is no appVersion.
test("the Hermes version comes from host.status()", async () => {
  const env = await collect({ status: async () => ({ version: "0.22.5", gateway_running: true }) });
  assert.equal(env.hermesVersion, "0.22.5");
});

test("a failing or missing host.status() reports 'unknown'", async () => {
  assert.equal((await collect({ status: async () => { throw new Error("down"); } })).hermesVersion, "unknown");
  assert.equal((await collect({})).hermesVersion, "unknown");
});

test("a host.status() that never answers does not hold the report up", async () => {
  const env = await collect({ status: () => new Promise(() => {}) });
  assert.equal(env.hermesVersion, "unknown");
});

test("no calls to host members the SDK does not have", () => {
  assert.ok(!src.includes("host.appVersion") && !src.includes("host?.appVersion"));
  assert.ok(!src.includes("host.hidePane"));
});
