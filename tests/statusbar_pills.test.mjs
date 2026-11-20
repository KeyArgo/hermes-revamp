// The Hermes status bar renders Aura-style pills: one now-playing pill (play
// state + ellipsised title) and pill buttons for prev / play-pause / next.
// Executes the real RevampBar and SmallAction closures with stub host parts,
// then checks the pill chrome contracts in the plugin CSS.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../desktop/plugin.js", import.meta.url), "utf8");

function between(start, end) {
  const a = src.indexOf(start);
  const b = src.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `source region found: ${start}`);
  return src.slice(a, b);
}

// A recorded jsx tree: enough to inspect what RevampBar actually renders.
// Function components (the SmallAction stub) are invoked; host parts stay inert.
const jsxStub = (type, props) => (typeof type === "function" ? type(props || {}) : { type, props: props || {} });
const jsxsStub = jsxStub;

// The real derivations, so the pill reflects actual status parsing. Strip the
// header and the function's own closing brace: new Function supplies both.
const nowPlayingFn = new Function("state", between("function nowPlaying(state) {", "\nfunction label(").replace("function nowPlaying(state) {", "").replace(/\}\s*$/, ""));
const labelFn = new Function("np", between("function label(np) {", "\n// One pet line").replace("function label(np) {", "").replace(/\}\s*$/, ""));

function renderBar({ playing = true, online = true, playlist = [], bar = "full" } = {}) {
  const body = between("function RevampBar() {", "\n// ---------------------------------------------------------------- page + card");
  const calls = { command: [], toggle: 0, smallActions: [], likes: 0 };
  const live = { live: { now_playing: { status: playing ? "PLAYING" : "PAUSED", title: "Long Song Title", artist: "Artist" }, playlist }, player_connected: online };
  const atom = v => ({ get: () => v });
  const EN = { browse: "Choose music", title: "REVamp", play: "Play", pause: "Pause", prev: "Previous", next: "Next", clearQueue: "Clear", clearConfirm: "Confirm clear", showFrame: "Show Frame", hideFrame: "Hide Frame" };
  const run = new Function(
    "jsx", "jsxs", "Fragment", "usePluginI18n", "useValue", "useState", "useEffect", "useRef",
    "$live", "$open", "$queueOpen", "$playerLocation", "$frameHidden", "$where", "$barControls",
    "nowPlaying", "label", "maybeAnnounce", "command", "toggle", "loadLikes",
    "BarToast", "Waveform", "Popover", "PopoverTrigger", "PopoverContent", "Button", "SmallAction",
    "QueuePanel", "Picker", "LocationRowWithTip", "ReportBugRow", "Signal", "Transport", "EmptyState",
    "heldQueue", "withHeld", "setFrameHidden", "setPlayerLocation", "icons", "DockIcon", "TrashIcon", "PrevIcon", "NextIcon",
    "ID",
    body + "\nreturn RevampBar();"
  );
  const tree = run(
    jsxStub, jsxsStub, "Fragment",
    () => k => EN[k] || k,
    a => a.get(),
    initial => [typeof initial === "function" ? initial() : initial, () => {}],
    () => undefined,
    () => ({ current: null }),
    atom(online ? live : null), atom(false), atom(false), atom("docked"), atom(false), atom("pane"), atom(bar),
    nowPlayingFn, labelFn, () => {},
    (...a) => calls.command.push(a), () => { calls.toggle++; }, () => { calls.likes++; },
    "BarToast", "Waveform", "Popover", "PopoverTrigger", "PopoverContent", "Button",
    (props) => { calls.smallActions.push(props); return { type: "SmallAction", props }; },
    "QueuePanel", "Picker", "LocationRowWithTip", "ReportBugRow", "Signal", "Transport", "EmptyState",
    null, s => s, () => {}, () => {},
    { Play: "PlayIcon", Pause: "PauseIcon" }, "DockIcon", "TrashIcon", "PrevIcon", "NextIcon",
    "revamp"
  );
  return { tree, calls };
}

// Walk the recorded tree for nodes of a given type.
function findAll(node, type, out = []) {
  if (!node || typeof node !== "object") return out;
  if (Array.isArray(node)) { node.forEach(n => findAll(n, type, out)); return out; }
  if (node.type === type) out.push(node);
  findAll(node.props?.children, type, out);
  return out;
}

test("the bar is a row of pills: now-playing pill plus prev / play-pause / next", () => {
  const { tree, calls } = renderBar({ playing: true });
  assert.equal(tree.type, "div");
  assert.equal(tree.props.className, "revamp-bar");
  assert.equal(tree.props["data-playing"], true);

  // The now-playing pill is the browse trigger: state glyph + ellipsised title.
  const triggers = findAll(tree, "Button").filter(n => String(n.props.className || "").includes("revamp-np-pill"));
  assert.equal(triggers.length, 1, "one now-playing pill");
  const np = triggers[0].props;
  assert.ok(np.className.includes("revamp-pill"), "pill chrome");
  assert.equal(np["aria-label"], "Choose music: Long Song Title — Artist");
  assert.equal(np["aria-haspopup"], "dialog");
  const kids = np.children.props.children;
  const state = kids.find(k => k.props?.className === "revamp-np-state");
  const name = kids.find(k => k.props?.className === "revamp-name");
  assert.ok(state && name, "play state + title inside the pill");
  assert.equal(state.props["aria-hidden"], true);
  assert.equal(state.props.children.type, "PauseIcon", "lit pause glyph while playing");
  assert.equal(name.props.children, "Long Song Title");

  // Transport pills, in order, each a labelled button.
  const transport = calls.smallActions.filter(a => ["Previous", "Pause", "Next"].includes(a.label));
  assert.deepEqual(transport.map(a => a.label), ["Previous", "Pause", "Next"]);
  for (const a of transport) {
    assert.ok(String(a.className).includes("revamp-pill"), `${a.label} is a pill`);
    assert.ok(String(a.className).includes("revamp-pill-icon"), `${a.label} is an icon pill`);
    assert.equal(typeof a.onClick, "function");
  }
  transport[0].onClick(); transport[1].onClick(); transport[2].onClick();
  assert.deepEqual(calls.command, [["prev"], ["next"]]);
  assert.equal(calls.toggle, 1, "play-pause uses the optimistic toggle");
});

test("paused state reads Play, and the pill keeps its box (no layout shift)", () => {
  const { tree, calls } = renderBar({ playing: false });
  assert.equal(tree.props["data-playing"], false);
  const np = findAll(tree, "Button").find(n => String(n.props.className || "").includes("revamp-np-pill"));
  const state = np.props.children.props.children.find(k => k.props?.className === "revamp-np-state");
  assert.equal(state.props.children.type, "PlayIcon", "dim play glyph while paused");
  const play = calls.smallActions.find(a => a.label === "Play");
  assert.ok(play, "the toggle pill says Play while paused");
  // Same pill classes in both states: the chrome never changes size.
  assert.ok(String(play.className).includes("revamp-pill-icon"));
});

test("offline: no transport pills, the now-playing pill still opens the panel", () => {
  const { tree, calls } = renderBar({ online: false });
  assert.deepEqual(calls.smallActions.filter(a => ["Previous", "Play", "Pause", "Next"].includes(a.label)), []);
  assert.ok(findAll(tree, "Button").some(n => String(n.props.className || "").includes("revamp-np-pill")));
});

test("SmallAction renders a real button with the pill class and an aria-label", () => {
  const body = between("function SmallAction({ label, icon, onClick", "\nfunction toggle()");
  const run = new Function("jsx", "Tip", "Button", "GlyphSpinner", body + "\nreturn SmallAction;");
  const SmallAction = run(jsxStub, "Tip", "Button", "GlyphSpinner");
  const node = SmallAction({ label: "Next", icon: "NextIcon", onClick: () => {}, className: "revamp-pill revamp-pill-icon" });
  assert.equal(node.type, "Tip");
  const btn = node.props.children;
  assert.equal(btn.type, "Button");
  assert.equal(btn.props["aria-label"], "Next");
  assert.equal(btn.props.title, "Next");
  assert.ok(btn.props.className.includes("revamp-pill revamp-pill-icon"));
  assert.ok(btn.props.className.includes("revamp-action"), "old class kept for the icon box");
});

test("pill chrome: Aura geometry on Hermes theme tokens, keyboard focus ring", () => {
  const pill = between(".revamp-pill{", "\n.revamp-pill:hover");
  assert.match(pill, /border-radius:999px/);
  assert.match(pill, /height:22px/);
  assert.match(pill, /border:1px solid var\(--ui-stroke-secondary\)/);
  assert.match(pill, /display:inline-flex/);
  assert.doesNotMatch(pill, /#[0-9a-f]{3,6}\b/i, "no hardcoded colours: light and dark themes both work");
  assert.match(src, /\.revamp-pill:hover\{background:var\(--chrome-action-hover\)/);
  assert.match(src, /\.revamp-pill:focus-visible\{outline:2px solid var\(--ui-accent\)/);
  assert.match(src, /\.revamp-pill\[aria-pressed=true\]\{border-color:var\(--ui-accent\)/);
  assert.match(src, /\.revamp-bar\{display:flex;align-items:center;gap:4px/, "Aura's 4px pill gap");
  // The pill rules come after the old 16px square, so they win the cascade.
  assert.ok(src.indexOf(".revamp-pill{") > src.indexOf(".revamp-action{"));
  // Title ellipsis survives inside the pill.
  assert.match(src, /\.revamp-np-pill \.revamp-name\{flex:0 1 auto;max-width:132px\}/);
  assert.match(src, /\.revamp-bar \.revamp-name\{[^}]*text-overflow:ellipsis/);
});

test("filter chips match the pills: full radius, same height, focus ring", () => {
  const chip = between(".revamp-chip{", "\n.revamp-chip:hover");
  assert.match(chip, /border-radius:999px/);
  assert.match(chip, /height:22px/);
  assert.match(src, /\.revamp-chip:focus-visible\{outline:2px solid var\(--ui-accent\)/);
  assert.match(src, /\.revamp-chip\[aria-pressed=true\]\{background:color-mix\(in srgb,var\(--ui-accent\) 12%,transparent\)\}/);
});

test("Menu only: the bar keeps just the REVamp pill, the menu keeps the controls", () => {
  const { tree, calls } = renderBar({ playing: true, playlist: [{ title: "a" }], bar: "menu" });
  assert.equal(calls.smallActions.length, 0, "no prev/play/next/clear/Frame pills");
  assert.equal(findAll(tree, "Waveform").length, 0, "no waveform");
  const np = findAll(tree, "Button").find(n => String(n.props.className || "").includes("revamp-np-pill"));
  assert.ok(np, "the REVamp menu pill stays");
  assert.ok(findAll(tree, "Transport").length >= 1, "transport still inside the menu");
  // Full controls (the default) still shows them.
  const full = renderBar({ playing: true, playlist: [{ title: "a" }] });
  assert.ok(full.calls.smallActions.length >= 4);
  assert.equal(findAll(full.tree, "Waveform").length, 1);
});

test("the Status bar setting is saved in ui.barControls and offered in Settings", () => {
  assert.match(src, /writeUiSettings\(\{ barControls: next \}\)/);
  assert.match(src, /savedUi\.barControls === 'menu' \? 'menu' : 'full'/);
  assert.match(src, /onClick: \(\) => setBarControls\('menu'\), children: t\('barControlsMenu'\)/);
  assert.match(src, /barControlsMenu: 'Menu only'/);
});
