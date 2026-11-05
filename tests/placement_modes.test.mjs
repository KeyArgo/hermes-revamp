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
  assert.ok(clear >= 0 && clear < back.indexOf("notifyError("),
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

test("docking back from the desktop loads the player where it goes, then nudges its size", () => {
  // Regression (user, 11-02): desktop -> "Dock it here" left the Frame white until Full
  // screen resized it. The page had loaded in the off-screen dock and was then moved with
  // moveBefore; Chromium never started drawing it. Verified live: 100% white -> 0%.
  const load = between("function loadWhereItGoes(src)", "function nudgeFrame(");
  const remove = load.indexOf("frame.remove()");
  const setSrc = load.indexOf("frame.src = src");
  const place = load.indexOf("place()");
  assert.ok(remove >= 0 && setSrc > remove && place > setSrc, "taken out, pointed at the page, then placed");
  assert.match(src, /park\(\) \{[^}]*loadWhereItGoes\(`\$\{url\}\/\?control=1`\)/);
  assert.match(src, /unpark\(\) \{[^}]*loadWhereItGoes\(`\$\{url\}\/\?embed=1`\)/);
  const ready = between("if (e.data.type === 'revamp:ready')", "if (e.data.type === 'revamp:bbox')");
  assert.ok(ready.indexOf("nudgeFrame()") > ready.indexOf("place()"), "size wiggle after the mode is re-sent");
});

test("a stale bridge key is refreshed, never a silent 5 s timeout per click (11-02)", () => {
  const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
  assert.match(app, /return reply\(\{ ok: false, status: 401, error: "bridge key", bridgeKey: true \}\);/);
  assert.match(src, /setKey\(k\) \{ key = k; bridgeApi\.ready = Boolean\(key\) && readyBridge \}/, "ready again once the key arrives");
  assert.match(src, /else if \(e\.data\.bridgeKey === true\) p\.reject\(Object\.assign\(new Error\('bridge key'\), \{ status: 401, bridgeKey: true \}\)\)/);
  const rest = between("async function rest(path, opts = {})", "let keyRefreshAt");
  assert.match(rest, /if \(err\?\.bridgeKey \|\| \(err\?\.status === 401 && \/invalid or missing token\/\.test\(err\.message \|\| ''\)\)\) void refreshBridgeKey\(\)/, "only the server's own key refusal refreshes the key");
  const refresh = between("async function refreshBridgeKey()", "const lastErrors");
  assert.match(refresh, /bridge\?\.setKey\?\.\(info\.bridge_key\)/);
  assert.match(refresh, /Date\.now\(\) - keyRefreshAt < 2_000/);
});

test("the same error shows once per 30 s, not once per click (11-02: 51 notifications)", () => {
  const body = between("function notifyError(message", "\n}\n");
  assert.match(body, /if \(!always && now - \(lastErrors\.get\(message\) \|\| 0\) < 30_000\) return/);
  assert.equal((src.match(/host\.notify\(\{ kind: 'error'/g) || []).length, 1, "every error goes through notifyError");
});

test("a server 401 through the bridge is a stale key: refresh, resend by the gateway, tell the host (Grok r2)", () => {
  const rest = between("async function rest(path, opts = {})", "let keyRefreshAt");
  assert.match(rest, /if \(err\?\.bridgeKey \|\| \(err\?\.status === 401 && \/invalid or missing token\/\.test\(err\.message \|\| ''\)\)\) void refreshBridgeKey\(\)/, "only the server's own key refusal refreshes the key");
  const refresh = between("async function refreshBridgeKey()", "const lastErrors");
  assert.ok(refresh.indexOf("keyRefreshAt = Date.now()") > refresh.indexOf("bridge?.setKey?.("), "throttle only after a refresh that worked");
  const onKey = between("if (e.data.type === 'revamp:key-rejected'", "if (e.data.type === 'revamp:bring-player-here')");
  assert.match(onKey, /controlFrame\.src = controlFrame\.src/, "the audio-free library frame reloads itself");
  assert.doesNotMatch(onKey, /frame\.src = frame\.src|\.reload\(\)/, "the player is never reloaded without the person choosing");
});

test("the library frame reloads at most once a minute after a refused key (no loop)", () => {
  const onKey = between("if (e.data.type === 'revamp:key-rejected'", "if (e.data.type === 'revamp:bring-player-here')");
  assert.match(onKey, /const wait = 60_000 - \(Date\.now\(\) - controlReloadAt\)/);
  assert.match(onKey, /else if \(!controlRetry\) controlRetry = setTimeout\(reloadControl, wait\)/, "refused again: one deferred retry");
});

test("Recover player's own failure always shows; the dedupe map stays small (GLM r5)", () => {
  assert.match(src, /notifyError\(EN\.recoveryFailed, \{ always: true \}\)/);
  assert.match(src, /if \(lastErrors\.size > 50\)/);
});

test("a successful command (not a background read) clears the duplicate-notice filter (GLM r7)", () => {
  const rest = between("async function rest(path, opts = {})", "let keyRefreshAt");
  assert.equal((rest.match(/if \(!read\) lastErrors\.clear\(\)/g) || []).length, 2);
  assert.doesNotMatch(rest, /^\s*lastErrors\.clear\(\)/m, "never cleared by a read");
});

test("Recover player cancels a pending automatic library-frame reload (GLM r9)", () => {
  assert.match(src, /reload\(\) \{ dropPending\(\); sawRevamp = false; clearTimeout\(controlRetry\); controlRetry = 0;/);
});

test("MilkDrop already closed while hidden stays closed when the placement changes (Codex a10r2)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  const start = player.indexOf("function suspendMilkdrop()");
  const body = player.slice(start, player.indexOf("function resumeMilkdrop()", start));
  assert.match(body, /if \(milkdropSuspended\) return;/);
  assert.doesNotMatch(body, /!milkdropOpen\(\)\) return/, "hidden is marked even when MilkDrop is closed");
  assert.match(body, /resumeMilkdropOnVisible = milkdropOpen\(\);/, "only reopened on show if it was open");
  assert.match(player, /if \(milkdropSuspended && want\) return;/, "placement changes cannot open it while hidden");
});

test("leaving full-screen MilkDrop forgets the fit skipped meanwhile (Claude a10r4)", () => {
  const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
  const start = app.indexOf("let wasFullscreen = false;");
  const body = app.slice(start, app.indexOf("}, 250);", start));
  assert.match(body, /if \(wasFullscreen && !now\) \{[\s\S]*lastPayload = ""; lastResizeKey = "";[\s\S]*kickDesktopFit\(\);/);
});

test("a tab picked while REVamp starts up stays picked (nav e2e flake, a10r7)", () => {
  const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
  assert.match(app, /function showStartView\(\) \{\n  if \(renderGeneration === 0\) setView\("library"\);\n\}/);
  assert.equal((app.match(/showStartView\(\);/g) || []).length, 2, "both start-up paths (page and Frame control)");
  assert.doesNotMatch(app, /\n\s*setView\("library"\);/, "no unconditional jump back to Library");
});

test("an open page compares its stamp with the files on disk, not the server's start (Grok a10r8)", () => {
  const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
  const start = app.indexOf("async function checkForUpdate()");
  const body = app.slice(start, app.indexOf("\n}\n", start));
  assert.match(body, /build = h\.files \|\| "";/, "an older server (no files) is never compared (Grok a10r10)");
  assert.doesNotMatch(body, /h\.build/);
});

test("hiding MilkDrop in Mini really closes it (toggleVisualizer refuses in Mini) (Grok a10r8)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  const start = player.indexOf("function suspendMilkdrop()");
  const body = player.slice(start, player.indexOf("function resumeMilkdrop()", start));
  assert.match(body, /webamp\.store\.dispatch\(\{ type: "TOGGLE_WINDOW", windowId: "milkdrop" \}\)/);
  assert.doesNotMatch(body, /api\.toggleVisualizer\(false\)/);
  assert.ok(body.indexOf("milkdropSuspended = true") < body.indexOf("TOGGLE_WINDOW"), "suspended first, so the close is not saved");
});

test("shown again, MilkDrop follows the saved choice for where it is now (Codex a10r10)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  const start = player.indexOf("function resumeMilkdrop()");
  const body = player.slice(start, player.indexOf("function syncMilkdropVisibility()", start));
  assert.match(body, /const want = hasWebGL\(\) && wantsMilkdropOpen\(\);/);
  assert.match(body, /if \(want !== milkdropOpen\(\)\) applyMilkdropChoice\(want\);/);
  assert.ok(body.indexOf("milkdropSuspended = false") < body.indexOf("applyMilkdropChoice"), "not suspended, or the open is dropped");
});

test("starting a book in several files goes on in the file you were last in (user report 11-03)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  const start = player.indexOf("async function maybeResume()");
  const body = player.slice(start, player.indexOf("// Once <audio> or Webamp knows the real length", start));
  assert.ok(body.indexOf("resumingId = rk") < body.indexOf("/api/resume/book"), "no 0:01 save while it looks");
  assert.match(body, /if \(!still \|\| gen !== handoffGen \|\| acted !== actSeq - transportSeq \|\| placeHere\(\)\s*\n\s*\|\| moved\(\)\) return;/,
    "a handoff or the person's own move wins, and stops the whole pending resume (Codex a10r16/a10r17)");
  assert.match(body, /if \(book\?\.id && book\.id !== tid\) \{/);
  assert.match(body, /api\.playIndex\(idx\);\n\s*return;/);
});

test("part 1 reached from inside the same book plays as asked (Claude a10r11)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  assert.match(player, /prevTid = playedTid; seenTrack = curTrack; seenTid = localTid\(info\.item\);/);
  assert.match(player, /"\/api\/resume\/book\?id=" \+ encodeURIComponent\(tid\) \+ from/);
  const start = player.indexOf("async function maybeResume()");
  assert.ok(player.indexOf("seenTrack = curTrack", start) < player.indexOf("if (info.status !== \"PLAYING\" || !rk) return;", start),
    "every track change is seen, music too, before the book-only early return");
  assert.ok(player.indexOf('if (info.status === "PLAYING") playedTid = localTid(info.item);', start) > 0
    && player.indexOf('if (info.status === "PLAYING") playedTid', start) < player.indexOf('if (info.status !== "PLAYING" || !rk) return;', start),
    "music that really played counts too");
});

test("a book's place near the end of a part is kept, not erased (Grok a10r11)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  const start = player.indexOf("function saveBookPlace(");
  const body = player.slice(start, player.indexOf("\n  }\n", start));
  assert.match(body, /const body = \{ key, position: pos \};/);
  assert.doesNotMatch(body, /position: finished \? null/);
});

test("back on part 1 after a continue, it resumes its own place (Grok a10r11)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  assert.match(player, /seenTid = localTid\(info\.item\);\n\s*resumedKey = null;/);
  assert.match(player, /resumingId = "local:track:" \+ book\.id;\n[^\n]*bookStart = [^\n]*\n\s*api\.playIndex\(idx\);/);
});

test("a continued book starts the part it moves to exactly where asked (Codex a10r12)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  const start = player.indexOf("async function maybeResume()");
  const body = player.slice(start, player.indexOf("// Once <audio> or Webamp knows the real length", start));
  assert.match(body, /if \(tid && bookStart\?\.tid === tid\) \{/);
  assert.ok(body.indexOf("bookStart?.tid === tid") < body.indexOf('apiGet("/api/resume?key='), "before the file's own (older) place");
  assert.match(body, /bookStart = \{ tid: book\.id, pos: Number\(book\.position\) \|\| 0 \};\n\s*api\.playIndex\(idx\);/);
  assert.match(body, /if \(book\?\.restart\) return;/, "a finished book starts part 1 from its start");
});

test("a continued part never seeks over the person's own seek or pause (Codex a10r13)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  assert.match(player, /gen === handoffGen && acted === actSeq - transportSeq\n\s*&& !placeHere\(\) && currentInfo\(\)\.status === "PLAYING"\n\s*&& \(media\(\)\.timeElapsed \|\| 0\) < heldBase \+ 10\n\s*&& !moved\(\)\) \{\n\s*keepResuming = true;\n\s*applySeek\(startAt, rk, gen, acted\);/,
    "Webamp's own seek bar (no actSeq) also wins (Codex a10r15/a10r16); a hold's clock widens the window (Grok a10r21)");
  assert.match(player, /if \(bookStart && bookStart\.tid !== seenTid\) bookStart = null;/, "a part that never started forgets it");
});


test("a queue restored at start-up, never played, doesn't block continuing a book (Claude a10r14)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  assert.match(player, /prevTid = playedTid;/, "before = the file that last really played");
  assert.match(player, /if \(mode === "replace"\) playedTid = null;/, "a book started from the list goes on");
  assert.ok(player.indexOf("let seenTrack = null") < player.indexOf("const api = {"), "declared before the api uses it");
});

test("Webamp's own buttons and seek bar stop a pending book jump (Codex a10r14/a10r16)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  assert.match(player, /const still = storeState\(\)\?\.playlist\?\.currentTrack === curTrack && currentInfo\(\)\.status === "PLAYING"\n\s*&& \(media\(\)\.timeElapsed \|\| 0\) < heldBase \+ 15;/);
  assert.match(player, /if \(!still \|\| gen !== handoffGen \|\| acted !== actSeq - transportSeq \|\| placeHere\(\)\s*\n\s*\|\| moved\(\)\) return;/,
    "not still: the whole pending resume stops (it used to fall through to the per-file place), and a native seek below 15 s counts");
});

test("a file that has not started is buffering, not a seek (Grok a10r18, Claude a10r19)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  // Nothing has played yet: 0:00 now is a slow disk, not the person moving.
  assert.match(player, /if \(now === 0\) return false;\s*\/\/ buffering/);
  // First audio at 0:10 means the person seeked there before it started.
  assert.match(player, /return start == null \? now > 5 : false;/);
  // After that, a real jump, scaled by playback speed and with room for a slow lookup.
  assert.match(player, /\* rate\(\)\)\) > 5/);
  // The baseline is sampled from the start of maybeResume, not from the first check.
  // The sampler starts only after the early returns, or ordinary playback leaks timers.
  assert.ok(player.indexOf("const sampler = setInterval") > player.indexOf("if (placeHere()) {"),
    "sampler starts after the early returns (Codex a10r20)");
  assert.match(player, /clearInterval\(sampler\);/);
  // Both seeks wait, and a start after the wait is still picked up.
  assert.match(player, /async function waitFirstAudio\(seconds = 15\)/);
  // The late path applies the same near-end and movement rules as the immediate one.
  assert.match(player, /if \(pos > 0 && !\(dur && pos >= dur - 30\) && !moved\(\)\) applySeek\(pos, rk, gen, acted\);/);
  assert.ok(player.includes("seekWhenStarted(rk, () => pos, gen, acted, moved);"),
    "a file that starts after the wait is still resumed (Codex a10r19)");
  assert.ok(player.includes("seekWhenStarted(rk, () => startAt, gen, acted, moved);"));
  // A pause during the wait keeps waiting; only gone/taken-over/never-started lets go.
  assert.match(player, /if \(\(media\(\)\.timeElapsed \|\| 0\) <= 0 \|\| currentInfo\(\)\.status !== "PLAYING"\) return;/);
  assert.match(player, /if \(resumingId === rk\) resumingId = null;\s*\n\s*return;/);
});


test("a hidden pop-out is not resized (Claude a10r19)", () => {
  const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
  const start = app.indexOf("function kickDesktopFit()");
  const body = app.slice(start, app.indexOf("\n}", start));
  assert.match(body, /if \(document\.hidden\) return;/, "a minimised/occluded window must not refit");
});

test("a seek that the element drops is retried, and the place is only released once it lands (Grok a10r19)", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  assert.match(player, /function applySeek\(pos, rk, gen, acted, tries = 3\)/,
    "every retry carries the handoff and action guards (Codex a10r20)");
  assert.match(player, /const gone = \(\) => resumeKeyOf\(currentInfo\(\)\.item\) !== rk \|\| gen !== handoffGen/);
  assert.match(player, /if \(Math\.abs\(now - pos\) <= 5\) \{/);
  assert.match(player, /if \(attempt < tries\) once\(\);/);
  // Retry, pause, held-seek and failure behavior is exercised in round21_behavior.test.mjs.
  // Both applies go through it, and each keeps resumingId until it resolves.
  assert.ok(player.includes("keepResuming = true;          // applySeek clears it once the seek has landed"));
  assert.ok(player.includes("applySeek(pos, rk, gen, acted);"));
  assert.ok(player.includes("applySeek(startAt, rk, gen, acted);"));
});

test("the Frame has a hide button that says how to bring it back", () => {
  const plugin = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");
  const header = plugin.slice(plugin.indexOf("function PaneHeaderButtons()"), plugin.indexOf("function ClassicPaneSpike()"));
  assert.match(header, /icon: icons\.X, onClick: hideFrameWithHint/);
  assert.match(plugin, /function hideFrameWithHint\(\) \{\n\s*setFrameHidden\(true\)\n\s*host\.notify\(/);
  assert.match(plugin, /\['frame', 'cmdFrame', .*\['mod\+alt\+f'\]\]/, "the shortcut the hint names is the real default");
});

test("the visualizer shortcut does not take Hermes's voice-input key", () => {
  const plugin = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");
  assert.match(plugin, /\['visualizer', 'cmdVisualizer', .*\['mod\+alt\+shift\+v'\]\]/);
});

test("a seek during a held resume is saved at once, and Play leaves it alone", () => {
  const player = readFileSync(new URL("../web/player.js", import.meta.url), "utf8");
  assert.match(player, /const noteSeek = \(target\) => \{[\s\S]*?releaseHold\?\.\(\);\n\s*\};/);
  const body = player.slice(player.indexOf("releaseHold = () =>"));
  assert.match(body.slice(0, 500), /resumedKey = `\$\{curTrack\(\)\}\|\$\{rk\}`/, "Play must not start the resume again");
  assert.match(body.slice(0, 500), /saveBookPlace\(true\)/);
  assert.match(player, /const pausedEarly = \(\) => [\s\S]{0,260}seekIntent\(curTrack\)\.version === startSeekVersion/,
    "a seek before the lookup returned is not held either");
});
