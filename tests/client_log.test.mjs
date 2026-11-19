import assert from "node:assert/strict";
import test from "node:test";
import { clientLogRecord } from "../web/diagnostics.js";

test("clientLogRecord bounds message and stack and strips URL queries/tokens", () => {
  const rec = clientLogRecord("error", {
    message: "x".repeat(900) + " http://127.0.0.1:8114/api/art/9?t=SECRET&y=2#z",
    stack: "y".repeat(2600),
  });
  assert.ok(rec.message.length <= 500);
  assert.ok(rec.stack.length <= 2000);
  assert.equal(rec.placement, "page");

  const short = clientLogRecord("error", {
    message: "failed http://127.0.0.1:8114/api/art/9?t=SECRET&y=2#z",
    stack: "at boot (http://127.0.0.1:8114/web/app.js?access_token=SECRET)",
  });
  assert.doesNotMatch(short.message, /SECRET|\?t=|y=2|#z/);
  assert.doesNotMatch(short.stack, /SECRET|access_token=/);
  assert.match(short.message, /\/api\/art\/9/);
  assert.match(short.stack, /\/web\/app\.js/);
});

test("clientLogRecord falls back to a plain label when there is no message", () => {
  assert.equal(clientLogRecord("rejection", {}).message, "Unhandled promise rejection");
  assert.equal(clientLogRecord("error", {}).message, "Unhandled browser error");
});

function harness(token) {
  const calls = [];
  const listeners = {};
  globalThis.window = {
    parent: { postMessage: () => {} },
    addEventListener: (k, f) => { listeners[k] = f; },
  };
  globalThis.document = {
    body: null,
    querySelector: (sel) => (sel === 'meta[name="revamp-token"]' && token ? { content: token } : null),
  };
  globalThis.location = { ancestorOrigins: ["file://"] };
  const realFetch = globalThis.fetch;
  globalThis.fetch = (url, opts) => { calls.push({ url, opts }); return Promise.resolve({ ok: true }); };
  return { calls, listeners, restore: () => {
    globalThis.fetch = realFetch;
    delete globalThis.window; delete globalThis.document; delete globalThis.location;
  } };
}

test("a page error is POSTed to /api/client_log with the token header", async () => {
  const h = harness("tok-123");
  await import("../web/diagnostics.js?post-error");
  h.listeners.error({ message: "boom in paint", error: { stack: "at paint (/web/player.js:1:1)" } });
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, "/api/client_log");
  assert.equal(h.calls[0].opts.method, "POST");
  assert.equal(h.calls[0].opts.headers["X-Revamp-Token"], "tok-123");
  const body = JSON.parse(h.calls[0].opts.body);
  assert.equal(body.message, "boom in paint");
  assert.match(body.stack, /\/web\/player\.js:1:1/);
  h.restore();
});

test("an unhandled rejection is POSTed too", async () => {
  const h = harness("tok-456");
  await import("../web/diagnostics.js?post-rejection");
  h.listeners.unhandledrejection({ reason: { message: "promise broke", stack: "s" } });
  assert.equal(h.calls.length, 1);
  assert.equal(JSON.parse(h.calls[0].opts.body).message, "promise broke");
  h.restore();
});

test("with no token the page does not POST (nothing to authenticate with)", async () => {
  const h = harness("");
  await import("../web/diagnostics.js?no-token");
  h.listeners.error({ message: "boom" });
  assert.equal(h.calls.length, 0);
  h.restore();
});
