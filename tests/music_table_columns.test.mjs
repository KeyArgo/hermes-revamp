// The Music table sits in the side panel (~440px even on a wide window). With a fixed
// table layout the Artist and Album columns were squeezed to "R… N…". The narrow rule
// must follow the panel's width, not the window's, and the actions column keeps its size.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../web/app.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

function block(start) {
  const i = css.indexOf(start);
  assert.notEqual(i, -1, `missing: ${start}`);
  let depth = 0;
  for (let j = css.indexOf("{", i); j < css.length; j++) {
    if (css[j] === "{") depth++;
    else if (css[j] === "}" && --depth === 0) return css.slice(i, j + 1);
  }
  return css.slice(i);
}

test("the panel body is a size container", () => {
  assert.match(block(".panel-body {"), /container-type:\s*inline-size/);
});

test("a narrow panel drops the Album column for music and keeps Artist", () => {
  const q = block("@container (max-width: 560px)");
  assert.match(q, /table\.list:not\(\.events\):not\(\.books\) th:nth-child\(4\)/);
  assert.match(q, /display:\s*none/);
  assert.doesNotMatch(q, /:not\(\.books\) td:nth-child\(3\)/, "Artist stays visible");
});

test("the actions column keeps its own width instead of the 64px last-child rule", () => {
  const m = css.match(/table\.list th\.more-cell\s*\{[^}]*width:\s*(\d+)px/);
  assert.ok(m, "missing table.list th.more-cell width");
  assert.ok(Number(m[1]) <= 36);
  assert.ok(css.indexOf("table.list th.more-cell") > css.indexOf("table.list th:last-child"));
});

const lib = readFileSync(new URL("../web/views/library.js", import.meta.url), "utf8");

test("a narrow panel lets Title and Artist share the room (Title is not pinned wide)", () => {
  const q = block("@container (max-width: 560px)");
  assert.match(q, /:not\(\.books\) th:nth-child\(2\)\s*\{\s*width:\s*auto/);
});

test("the Artists list counts albums (the backend sends their names) and says 1 album", () => {
  const line = lib.split("\n").find((l) => l.includes("tdN.textContent"));
  assert.ok(line, "missing the artist count line");
  assert.doesNotMatch(line, /\$\{a\.albums\} albums/, "prints the album names");
  assert.match(lib, /function countLabel\(/);
});

test("the artist name is not squeezed into the 36px number column", () => {
  assert.match(lib, /tbl\.className = "list artists"/);
  assert.match(css, /table\.list\.artists td:first-child\s*\{[^}]*width:\s*auto/);
});
