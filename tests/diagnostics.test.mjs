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
  await import("../web/diagnostics.js?loop");
  for (let i = 0; i < 100; i++) listeners.error({ message: "boom in raf" });
  assert.equal(sent.length, 1);
  delete globalThis.window; delete globalThis.document;
});

test("the player's own script locations stay readable", () => {
  assert.match(diagnosticRecord("error", { stack: "at paint (/web/player.js:12:3)" }).stack, /\/web\/player\.js:12/);
});
