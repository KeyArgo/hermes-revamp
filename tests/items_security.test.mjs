import assert from "node:assert/strict";
import test from "node:test";
import { normalizeItem } from "../web/items.js";

// Security audit 11-02 (finding 2): an item from a shared playlist or a command must never
// hand an outside address straight to the audio element.
const base = { id: "url:x", source: "url", kind: "music", title: "t" };

test("outside addresses play through the checked stream proxy", () => {
  const it = normalizeItem({ ...base, playback: { backend: "webamp", url: "http://172.16.0.1/reboot" } });
  assert.match(it.playback.url, /^\/api\/radio\/stream\?url=/);
  assert.equal(it.playback.stream, "http://172.16.0.1/reboot");
});

test("non-route addresses are refused", () => {
  for (const url of ["file:///etc/passwd", "javascript:alert(1)", "//evil.example/x", "data:audio/mp3;base64,AA", "blob:x",
                     "/\\172.16.0.1/admin", "/api/media/a\\b", "/elsewhere"]) {
    assert.equal(normalizeItem({ ...base, playback: { backend: "webamp", url } }), null, url);
  }
});

test("REVamp's own routes are untouched", () => {
  const it = normalizeItem({ ...base, playback: { backend: "webamp", url: "/api/media/abc" } });
  assert.equal(it.playback.url, "/api/media/abc");
});

test("item art is a local route or goes through the logo proxy", () => {
  const it = (art) => normalizeItem({ ...base, art, playback: { backend: "webamp", url: "/api/media/abc" } }).art;
  assert.match(it("http://172.16.0.1/x.png"), /^\/api\/radio\/favicon\?url=/);
  for (const bad of ["/\\evil/x.png", "javascript:x", "//evil/x"]) assert.equal(it(bad), null, bad);
  assert.equal(it("/api/art/abc"), "/api/art/abc");
});

test("dot segments and unknown backends are refused (round 3)", () => {
  for (const url of ["/api/sources/../backups/export", "/api/sources/%2e%2e/backups/export", "/api/media/.%2E/x"]) {
    assert.equal(normalizeItem({ ...base, playback: { backend: "webamp", url } }), null, url);
  }
  // Extension backends are kept, but their URL is vetted like any other.
  assert.match(normalizeItem({ ...base, playback: { backend: "evil", url: "http://172.16.0.1" } }).playback.url, /^\/api\/radio\/stream\?url=/);
  assert.equal(normalizeItem({ ...base, playback: { backend: "evil", url: "javascript:x" } }), null);
  // Source refs carry encoded slashes; they must keep working.
  assert.ok(normalizeItem({ ...base, playback: { backend: "webamp", url: "/api/sources/plex@abc/stream/track%2F5531" } }));
});
