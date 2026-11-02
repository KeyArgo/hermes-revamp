import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");

function between(start, end) {
  const a = src.indexOf(start);
  assert.notEqual(a, -1, start);
  const b = src.indexOf(end, a);
  assert.notEqual(b, -1, end);
  return src.slice(a, b);
}

test("the location menu names are the five the user asked for", () => {
  for (const [key, label] of [
    ["locMini", "Mini player"],
    ["locDocked", "Frame"],
    ["locFloating", "Pop into Hermes"],
    ["locDesktop", "Pop out to desktop"],
    ["locFull", "Full screen"],
  ]) {
    assert.match(src, new RegExp(`${key}: '${label}'`));
  }
});

test("placeItems offers them in order, with the frame side in the name", () => {
  const body = between("function placeItems(", "function LocationRow(");
  const order = [...body.matchAll(/item\('(\w+)'|extra\('(\w+)'/g)].map((m) => m[1] || m[2]);
  assert.deepEqual(order, ["mini", "docked", "frame", "floating", "desktop", "full"]);
  assert.ok(body.includes("item('docked', 'locDocked', ` (${side})`)"));
});

test("Frame can be hidden without changing its selected location, and restored on its saved side", () => {
  assert.match(src, /const \$frameHidden = atom\(false\)/);
  assert.match(src, /ctxRef\?\.storage\.set\('frameHidden', hidden\)/);
  assert.match(src, /host\.revealPane\?\.\(paneId\)/);
  assert.match(src, /\['frame', 'cmdFrame'.*\['mod\+alt\+f'\]/);
  assert.match(src, /const loc = \$playerLocation\.get\(\)\s+return loc/);
});

test("hiding Frame unregisters its pane so Hermes has no empty container", () => {
  const body = between("function setFrameHidden(hidden)", "function dockedPlayerPane(");
  const hidden = body.slice(body.indexOf("if (hidden)"), body.indexOf("} else {"));
  const dispose = hidden.indexOf("disposeDockedPlayerPane?.()");
  const clear = hidden.indexOf("disposeDockedPlayerPane = null");
  const hide = hidden.indexOf("host.hidePane(paneId)");
  assert.ok(dispose >= 0 && clear > dispose && hide > clear);
  assert.match(body, /if \(!disposeDockedPlayerPane && PANE_SPIKE && ctxRef\) disposeDockedPlayerPane = ctxRef\.register/);
});

test("Frame uses Hermes's normal closable-tab chrome and can be restored", () => {
  const pane = between("function dockedPlayerPane(side)", "// A contribution registration");
  const bar = between("function RevampBar()", "function PaneHeaderButtons()");
  assert.doesNotMatch(pane, /hideOnly:\s*true/);
  assert.match(bar, /const frameOpen = docked && !frameHidden && where === 'pane'/);
  assert.match(bar, /onClick: \(\) => frameOpen \? setFrameHidden\(true\) : setPlayerLocation\('docked'\)/);
});

test("placementLocation never collapses a Frame to the mini player", () => {
  const body = between("function placementLocation()", "const LOC_BUS");
  assert.match(body, /const loc = \$playerLocation\.get\(\)\s+return loc/);
  assert.doesNotMatch(body, /mini|frameHidden|DOCKED_MIN_WIDTH|innerWidth/);
});

test("choosing the frame reveals the pane instead of leaving it dismissed", () => {
  const body = between("function setPlayerLocation(", "function dockedPlayerPane(");
  assert.match(body, /loc === 'docked'/);
  assert.match(body, /host\.revealPane\?\.\(scopedPaneId\(\$dockedPlayerSide\.get\(\)\)\)/);
});

test("picking any place while the desktop window plays brings the player back", () => {
  // Bug: Frame, "Dock it here" and the mini player's "put in frame" only saved the
  // location; the desktop window kept the player until its X was pressed.
  const body = between("function setPlayerLocation(", "function setFrameHidden(");
  const atom = (v) => ({ get: () => v, set: (n) => { v = n } });
  const calls = [];
  const run = new Function(
    "PLAYER_LOCATIONS", "$where", "$pageBorrow", "$playerLocation", "$winampOpen", "$frameHidden",
    "$dockedPlayerSide", "$desktop", "ctxRef", "locBus", "locSender", "host", "engine",
    "setFrameHidden", "scopedPaneId", "bringBackFromDesktop", "deskBusy",
    `let pendingBringBack = false; ${body}; return { set: setPlayerLocation, pending: () => pendingBringBack }`);
  const make = (desktopOpen, busy = false) => run(
    ["mini", "docked", "floating"], atom("desktop"), atom(false), atom("mini"), atom(false), atom(false),
    atom("right"), atom(desktopOpen), { storage: { set() {} } }, { postMessage() {} }, "me",
    { revealPane() {} }, { place: () => calls.push("place") },
    () => {}, (s) => s, () => calls.push("bringBack"), busy);
  for (const loc of ["docked", "mini", "floating"]) {
    calls.length = 0;
    make(true).set(loc);
    assert.deepEqual(calls, ["bringBack"], `${loc} while popped out`);
  }
  calls.length = 0;
  make(true).set("docked", true); // another window's echo must not close it
  assert.ok(!calls.includes("bringBack"));
  calls.length = 0;
  make(false).set("docked");
  assert.deepEqual(calls, ["place"]);
  // A click while an open or close is in flight starts nothing at once: it is remembered
  // and applied when that finishes (settlePendingPlace). During an open $desktop is still
  // false, so without this the pop-out took the player away right after "here" was picked.
  for (const open of [true, false]) {
    calls.length = 0;
    const p = make(open, true);
    p.set("docked");
    assert.deepEqual(calls, [], `in flight, desktop=${open}`);
    assert.equal(p.pending(), true);
  }
});

test("a place picked during the pop-out's open is applied once it finishes", () => {
  const settle = between("function settlePendingPlace()", "\n}") + "\n}";
  const run = new Function("$desktop", "bringBackFromDesktop", "engine",
    `let pendingBringBack = true; ${settle}; settlePendingPlace(); return pendingBringBack`);
  const calls = [];
  const left = run({ get: () => true }, () => calls.push("bringBack"), { place: () => calls.push("place") });
  assert.deepEqual(calls, ["bringBack"]);
  assert.equal(left, false);
  const open = between("async function openOnDesktop()", "async function bringBackFromDesktop()");
  assert.ok(open.indexOf("settlePendingPlace()") > open.indexOf("deskBusy = false"));
  const back = between("async function bringBackFromDesktop()", "// Play now");
  assert.ok(back.indexOf("settlePendingPlace") > back.indexOf("deskBusy = false"));
});

test("a second Pop out while an open or close runs starts nothing", () => {
  const open = between("async function openOnDesktop()", "async function bringBackFromDesktop()");
  const guard = open.indexOf("if (deskBusy) return");
  assert.ok(guard >= 0 && guard < open.indexOf("deskBusy = true"));
  const back = between("async function bringBackFromDesktop()", "// Play now");
  const clear = back.indexOf("pendingBringBack = false");
  assert.ok(clear >= 0 && clear < back.indexOf("host.notify"),
    "a failed close must not also retry silently behind its 'close it by hand' message");
});

test("an open that timed out but did open the window is noticed before a pending pick is settled", () => {
  const open = between("async function openOnDesktop()", "async function bringBackFromDesktop()");
  const c = open.indexOf("} catch (err) {");
  const ask = open.indexOf("rest('/window'", c);
  assert.ok(c >= 0 && ask > c && ask < open.indexOf("} finally {"));
  assert.match(open.slice(c), /if \(win\?\.open === true\) \$desktop\.set\(true\)/);
});

test("after a timed-out open, the window is looked for more than once", () => {
  const open = between("async function openOnDesktop()", "async function bringBackFromDesktop()");
  const c = open.slice(open.indexOf("} catch (err) {"), open.indexOf("} finally {"));
  assert.match(c, /for \(let i = 0; i < 3 && win\?\.open !== true; i\+\+\)/);
});

test("the bring-back itself refuses to overlap, whatever calls it (the shortcut too)", () => {
  const back = between("async function bringBackFromDesktop()", "// Play now");
  const guard = back.indexOf("if (deskBusy) { pendingBringBack = true; return }");
  assert.ok(guard >= 0 && guard < back.indexOf("deskBusy = true"));
});
