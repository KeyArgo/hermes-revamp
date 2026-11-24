import assert from "node:assert/strict";
import test from "node:test";
import { diagnosticRecord } from "../web/diagnostics.js";

test("browser diagnostics are bounded and do not retain paths, queries, or tokens", () => {
  const entry = diagnosticRecord("error", {
    message: "failed at /home/alice/Music/private.mp3?token=secret",
    stack: "Error: boom\nhttp://127.0.0.1:8114/app.js?access_token=secret",
    placement: "page",
  });
  assert.equal(entry.kind, "error");
  assert.equal(entry.placement, "page");
  assert.ok(entry.message.length <= 240);
  assert.doesNotMatch(entry.message, /alice|secret|private\.mp3/);
  assert.doesNotMatch(entry.stack, /secret|\?token|access_token/);
});

test("library paths outside home are redacted too", () => {
  for (const p of ["/mnt/music/Bob/song.mp3", "/media/bob/usb/a.flac", "/srv/share/x.ogg", "/run/media/bob/disk/y.mp3", "/music/bob/song.mp3"]) {
    assert.doesNotMatch(diagnosticRecord("error", { message: `failed at ${p}` }).message, /bob|Bob|song|share/, p);
  }
});

test("an error thrown every frame is passed on once", async () => {
  const sent = [];
  const listeners = {};
  globalThis.window = {
    parent: { postMessage: (m) => sent.push(m) },
    addEventListener: (k, f) => { listeners[k] = f; },
  };
  globalThis.document = { body: null };
  globalThis.location = { ancestorOrigins: ["file://"] }; // framed by Hermes (a trusted parent)
  await import("../web/diagnostics.js?loop");
  for (let i = 0; i < 100; i++) listeners.error({ message: "boom in raf" });
  assert.equal(sent.length, 1);
  delete globalThis.window; delete globalThis.document; delete globalThis.location;
});

test("the player's own script locations stay readable", () => {
  assert.match(diagnosticRecord("error", { stack: "at paint (/web/player.js:12:3)" }).stack, /\/web\/player\.js:12/);
});

test("a second, different error right after the first is passed on too (a moment later)", async () => {
  // Hunt 3: the once-a-second limit dropped it for good, and in a start-up cascade the
  // second error is often the useful one.
  const sent = [];
  const listeners = {};
  globalThis.window = {
    parent: { postMessage: (m) => sent.push(m) },
    addEventListener: (k, f) => { listeners[k] = f; },
  };
  globalThis.document = { body: null };
  globalThis.location = { ancestorOrigins: ["file://"] };
  await import("../web/diagnostics.js?cascade");
  listeners.error({ message: "first failure" });
  await new Promise((r) => setTimeout(r, 10));
  listeners.unhandledrejection({ reason: { message: "second failure" } });
  listeners.unhandledrejection({ reason: { message: "second failure" } }); // the same one again: still once
  assert.deepEqual(sent.map((m) => m.record.message), ["first failure"], "at most one a second");
  await new Promise((r) => setTimeout(r, 1100));
  assert.deepEqual(sent.map((m) => m.record.message), ["first failure", "second failure"]);
  delete globalThis.window; delete globalThis.document; delete globalThis.location;
});
