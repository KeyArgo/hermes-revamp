import assert from "node:assert/strict";
import test from "node:test";

// Security audit 11-02 (finding 4): a web site framing the token-bearing page must get
// nothing: the page refuses to run, and diagnostics are not posted out.
async function check({ framed = true, ancestors = null, referrer = "" }) {
  const self = {};
  globalThis.window = framed ? { parent: {} } : self;
  if (!framed) window.parent = window;
  globalThis.location = { ancestorOrigins: ancestors };
  globalThis.document = { referrer };
  const mod = await import(`../web/embed-trust.js?${Math.random()}`);
  return mod.untrustedEmbed();
}

test("Hermes, the pop-out and this computer may embed the page", async () => {
  assert.equal(await check({ framed: false }), false);
  for (const o of ["null", "file://", "app://hermes", "http://127.0.0.1:9119", "http://localhost:5173", "http://[::1]:8114"]) {
    assert.equal(await check({ ancestors: [o] }), false, o);
  }
});

test("a web site framing it (directly or further up) is refused", async () => {
  assert.equal(await check({ ancestors: ["https://evil.example"] }), true);
  assert.equal(await check({ ancestors: ["null", "https://evil.example"] }), true, "a sandboxed frame inside a site");
  assert.equal(await check({ ancestors: ["http://127.0.0.1.evil.example"] }), true);
  assert.equal(await check({ ancestors: [], referrer: "https://evil.example/page" }), true, "no ancestorOrigins: referrer");
});

test("the app refuses before reading the token; diagnostics check it before posting", async () => {
  const { readFileSync } = await import("node:fs");
  const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
  assert.ok(app.indexOf("if (untrustedEmbed()) {") < app.indexOf("const TOKEN"));
  const diag = readFileSync(new URL("../web/diagnostics.js", import.meta.url), "utf8");
  assert.match(diag, /window\.parent !== window && !untrustedEmbed\(\)/);
});

test("framed with no way to tell the parent (hidden referrer, no ancestorOrigins): refused", async () => {
  // Audit round 2 (Codex): this used to fail open.
  assert.equal(await check({ ancestors: null, referrer: "" }), true);
  assert.equal(await check({ ancestors: [], referrer: "" }), true);
});
