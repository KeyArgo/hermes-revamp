import assert from "node:assert/strict";
import test from "node:test";

import { contentSize, outerWindowSize } from "../web/float-layout.js";

test("content size is the stack from the origin", () => {
  const box = contentSize([
    { left: 0, top: 0, right: 275, bottom: 116, width: 275, height: 116 },
    { left: 0, top: 116, right: 275, bottom: 232, width: 275, height: 116 },
    { left: 0, top: 232, right: 275, bottom: 464, width: 275, height: 232 },
  ]);
  assert.deepEqual(box, { x: 0, y: 0, w: 275, h: 464 });
});

test("an unpinned window still counts so the resize cannot clip it", () => {
  const box = contentSize([
    { left: 40, top: 20, right: 315, bottom: 136, width: 275, height: 116 },
  ]);
  assert.equal(box.x, 40);
  assert.equal(box.y, 20);
  assert.equal(box.w, 315);
  assert.equal(box.h, 136);
});

test("milkdrop beside the stack widens the box and keeps one height", () => {
  const box = contentSize([
    { left: 0, top: 0, right: 275, bottom: 464, width: 275, height: 464 },
    { left: 275, top: 0, right: 550, bottom: 464, width: 275, height: 464 },
  ]);
  assert.deepEqual(box, { x: 0, y: 0, w: 550, h: 464 });
});

test("outer size adds the window chrome and never goes negative", () => {
  assert.deepEqual(outerWindowSize(275, 464, 800, 600, 816, 639), { width: 291, height: 503 });
  assert.deepEqual(outerWindowSize(275, 464, 275, 464, 275, 464), { width: 275, height: 464 });
  assert.deepEqual(outerWindowSize(275, 464, 900, 700, 800, 600), { width: 275, height: 464 });
});

test("empty and tiny rects are ignored", () => {
  assert.equal(contentSize([]), null);
  assert.equal(contentSize([{ left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }]), null);
});
