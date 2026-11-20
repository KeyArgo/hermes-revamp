// A name that is used but never declared or imported throws a ReferenceError at run time.
// Several hid inside try/catch or a resize listener and silently broke a feature:
// the empty-Playlists Music Studio hint (mountHint/STUDIO_LINE never imported), a resize
// listener that threw on every resize (DOCKED_MIN_WIDTH deleted), and a cross-window post to
// a channel that no longer existed (locBus/locSender).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";

const read = p => readFileSync(new URL(`../${p}`, import.meta.url), "utf8");
const strip = src => src
  .replace(/\/\*[\s\S]*?\*\//g, " ")
  .replace(/(^|[^:\\])\/\/.*$/gm, "$1")
  .replace(/`(?:\\[\s\S]|\$\{[^}]*\}|[^`\\])*`/g, "``")
  .replace(/'(?:\\.|[^'\\\n])*'/g, "''")
  .replace(/"(?:\\.|[^"\\\n])*"/g, '""');

// Every CONSTANT_CASE name and every $atom used must be declared or imported in the file.
function undeclared(src) {
  const code = strip(src);
  // Declarations are read from the raw text: a declaration is never inside a string.
  const declared = new Set();
  for (const m of src.matchAll(/\b(?:const|let|var|function|class)\s+([$\w]+)/g)) declared.add(m[1]);
  for (const m of src.matchAll(/\b(?:const|let|var)\s*\{([^}]*)\}/g))
    for (const part of m[1].split(",")) { const n = part.split(":").pop().split("=")[0].trim(); if (n) declared.add(n); }
  for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from/g))
    for (const part of m[1].split(",")) { const n = part.split(/\s+as\s+/).pop().trim(); if (n) declared.add(n); }
  for (const m of src.matchAll(/import\s+([$\w]+)\s+from/g)) declared.add(m[1]);
  const used = new Set();
  for (const m of code.matchAll(/(?<![.\w$])(\$[a-zA-Z]\w*|[A-Z][A-Z0-9_]{2,})\b/g)) used.add(m[1]);
  const globals = new Set(["JSON", "NaN", "URL", "CSS", "DOM", "API", "ID", "EN", "UTF", "HTTP"]);
  return [...used].filter(n => !declared.has(n) && !globals.has(n) && !(n in globalThis));
}

test("desktop/plugin.js uses no undeclared constants or atoms", () => {
  assert.deepEqual(undeclared(read("desktop/plugin.js")), []);
});

test("web views use no undeclared constants or atoms", () => {
  for (const f of readdirSync(new URL("../web/views/", import.meta.url)).filter(f => f.endsWith(".js"))) {
    assert.deepEqual(undeclared(read(`web/views/${f}`)), [], f);
  }
});

test("views that call mountHint import it", () => {
  for (const f of readdirSync(new URL("../web/views/", import.meta.url)).filter(f => f.endsWith(".js") && f !== "siblings.js")) {
    const src = read(`web/views/${f}`);
    if (/\bmountHint\(/.test(src)) assert.match(src, /import\s*\{[^}]*\bmountHint\b[^}]*\}\s*from\s*"\.\/siblings\.js"/, f);
  }
});

test("the deleted narrow-window resize listener and location channel stay gone", () => {
  const src = read("desktop/plugin.js");
  assert.doesNotMatch(src, /DOCKED_MIN_WIDTH|locBus|locSender/);
});
