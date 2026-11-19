// Inside Hermes the player page is a sandboxed iframe with no allow-popups, so
// window.open and target="_blank" do nothing. Both halves of the fix: the page
// asks its host to open the URL, and the host accepts it only from REVamp's own
// frames and only for http(s) URLs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { isEmbedded, openExternal } from "../web/open-external.js";

const plugin = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");
const view = name => readFileSync(new URL(`../web/views/${name}`, import.meta.url), "utf8");

test("embedded: the page asks its host instead of opening a window", () => {
  const posted = [];
  globalThis.window = {
    parent: { postMessage: (msg, origin) => posted.push([msg, origin]) },
    open: () => { throw new Error("window.open must not run inside Hermes"); },
  };
  assert.equal(isEmbedded(), true);
  assert.equal(openExternal("https://skins.webamp.org"), true);
  assert.deepEqual(posted, [
    [{ type: "revamp:open-external", url: "https://skins.webamp.org" }, "*"],
  ]);
});

test("not embedded: window.open still works", () => {
  const opened = [];
  const w = { open: (url, target, features) => { opened.push([url, target, features]); return {}; } };
  w.parent = w;
  globalThis.window = w;
  assert.equal(isEmbedded(), false);
  assert.equal(openExternal("https://example.test/page"), true);
  assert.deepEqual(opened, [["https://example.test/page", "_blank", "noopener,noreferrer"]]);
});

test("only http(s) URLs are handed over", () => {
  const posted = [];
  globalThis.window = {
    parent: { postMessage: (msg) => posted.push(msg) },
    open: () => { throw new Error("must not open"); },
  };
  assert.equal(openExternal("javascript:alert(1)"), false);
  assert.equal(openExternal("ftp://example.test/x"), false);
  assert.equal(openExternal(undefined), false);
  assert.deepEqual(posted, []);
});

test("the host accepts revamp:open-external only from REVamp's frames and only http(s)", () => {
  const block = plugin.slice(
    plugin.indexOf("if (fromPage && e.data.type === 'revamp:open-external')"),
    plugin.indexOf("// The server refused a page's key")
  );
  assert.match(plugin, /const fromPage = e\.source === frame\.contentWindow \|\| e\.source === controlFrame\.contentWindow/);
  assert.match(block, /if \(fromPage && e\.data\.type === 'revamp:open-external'\)/);
  assert.match(block, /typeof u === 'string' && \/\^https\?:\\\/\\\/\/i\.test\(u\)/);
  assert.match(block, /typeof ctxRef\?\.os\?\.openExternal === 'function'/);
  assert.match(block, /ctxRef\.os\.openExternal\(u\)/);
});

test("the views route external links through the helper, not window.open", () => {
  const agent = view("agent.js");
  const radio = view("radio.js");
  const skins = view("skins.js");
  assert.match(agent, /import \{ openExternal \} from "\.\.\/open-external\.js"/);
  assert.match(agent, /openExternal\(buildBugReportUrl\(env\)\)/);
  assert.match(radio, /import \{ openExternal \} from "\.\.\/open-external\.js"/);
  assert.match(radio, /addItem\("🌐 Open website", \(\) => openExternal\(st\.homepage\)\)/);
  assert.match(skins, /import \{ openExternal \} from "\.\.\/open-external\.js"/);
  assert.match(skins, /openExternal\("https:\/\/skins\.webamp\.org"\)/);
  for (const [name, src] of [["agent.js", agent], ["radio.js", radio], ["skins.js", skins]]) {
    assert.ok(!src.includes("window.open("), `${name} must not call window.open`);
  }
});
