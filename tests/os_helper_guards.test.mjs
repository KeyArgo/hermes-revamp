// ctxRef.os is optional: a host without writeClipboard/openExternal must get a
// plain notice instead of a TypeError from the bug-report actions.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");

test("the bug-report actions guard the optional os helpers", () => {
  const fn = src.slice(
    src.indexOf("async function openBugReport"),
    src.indexOf("function readPlaceMenuTipSeen")
  );
  assert.match(fn, /typeof ctxRef\.os\?\.writeClipboard !== 'function'/);
  assert.match(fn, /typeof ctxRef\.os\?\.openExternal !== 'function'/);
  assert.match(fn, /notifyError\('Copying to the clipboard is not available in this host\.'\)/);
  assert.match(fn, /notifyError\('Opening a link is not available in this host\.'\)/);
  // Both calls stay behind the guard.
  const copyGuard = fn.indexOf("typeof ctxRef.os?.writeClipboard !== 'function'");
  const copyCall = fn.indexOf("ctxRef.os.writeClipboard(");
  const openGuard = fn.indexOf("typeof ctxRef.os?.openExternal !== 'function'");
  const openCall = fn.indexOf("ctxRef.os.openExternal(");
  assert.ok(copyGuard >= 0 && copyCall > copyGuard, "writeClipboard is called only after its guard");
  assert.ok(openGuard >= 0 && openCall > openGuard, "openExternal is called only after its guard");
});
