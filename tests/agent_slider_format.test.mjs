// Settings showed "Dip length 200%": every slider printed its value as a percentage, and the
// dip length is a number of seconds (1-5).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../web/views/agent.js", import.meta.url), "utf8");
const start = src.indexOf("  const mkSlider = (");
const end = src.indexOf("\n  };\n", start) + 5;
const sliderSrc = src.slice(start, end);

function el(tag) {
  const ls = {};
  return { tag, children: [], attrs: {}, textContent: "", value: "",
    append(...k) { this.children.push(...k); }, setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, f) { ls[t] = f; }, fire(t) { ls[t]?.(); } };
}
const document = { createElement: el };
const mkSlider = new Function("document", `${sliderSrc}\nreturn mkSlider;`)(document);

function findCall(label) {
  const line = src.split("\n").find((l) => l.includes(`mkSlider("${label}"`));
  assert.ok(line, `missing ${label}`);
  return line;
}

test("the Dip length reads in seconds, before and after it moves", () => {
  const line = findCall("Dip length");
  const fmt = line.match(/,\s*(\(v\) => `[^`]*`)\)\);\s*$/);
  assert.ok(fmt, "Dip length passes a formatter");
  const format = new Function(`return ${fmt[1]}`)();
  const row = mkSlider("Dip length", 2, 1, 5, 1, () => {}, format);
  const [, rng, val] = row.children;
  assert.equal(val.textContent, "2 s");
  rng.value = "4";
  rng.fire("input");
  assert.equal(val.textContent, "4 s");
});

test("a slider without a formatter still shows a percentage (Duck level)", () => {
  const row = mkSlider("Duck level", 0.25, 0.05, 1, 0.05, () => {});
  assert.equal(row.children[2].textContent, "25%");
  assert.doesNotMatch(findCall("Duck level"), /`\$\{v\} s`/);
});
