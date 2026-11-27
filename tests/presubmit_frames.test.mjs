// The player frames' sandbox flags and the Radio coexistence bus: both must match what the
// page really needs and where the channel really comes from.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = fileURLToPath(new URL("..", import.meta.url));
const src = readFileSync(join(root, "desktop", "plugin.js"), "utf8");

function webFiles(dir = join(root, "web"), out = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (name === "vendor") continue;  // third-party bundles: not our markup
    if (statSync(path).isDirectory()) webFiles(path, out);
    else if (/\.(js|html)$/.test(name)) out.push(path);
  }
  return out;
}

test("player frames grant scripts and downloads, never forms", () => {
  const sandboxes = [...src.matchAll(/setAttribute\('sandbox', '([^']+)'\)/g)].map(m => m[1]);
  assert.equal(sandboxes.length, 2, "both player iframes set a sandbox");
  for (const flags of sandboxes) {
    assert.equal(flags, "allow-scripts allow-downloads", flags);
  }
});

test("the page really saves files itself, which is why allow-downloads stays", () => {
  const backups = readFileSync(join(root, "web", "views", "backups.js"), "utf8");
  const playlists = readFileSync(join(root, "web", "views", "playlists.js"), "utf8");
  assert.match(backups, /a\.download = "revamp-backup\.zip"/, "the backup export downloads a zip");
  assert.match(playlists, /a\.download = ""/, "a playlist export downloads a file");
});

test("web/ submits no form anywhere", () => {
  const forms = webFiles().filter(path => /<form\b/i.test(readFileSync(path, "utf8")));
  assert.deepEqual(forms, [], `a form would need allow-forms: ${forms.join(", ")}`);
});

test("postMessage '*' is explained: the frame has an opaque origin", () => {
  assert.match(src, /postMessage\(msg, '\*'\)/);
  assert.match(src, /origin is opaque/);
  assert.match(src, /no concrete target origin can match it/);
});

test("the Radio bus is kept and names the file that publishes it", () => {
  assert.match(src, /const RADIO_BUS = 'hermes:radio:playback'/);
  assert.match(src, /apps\/desktop\/src\/plugins\/radio\/plugin\.js/);
});
