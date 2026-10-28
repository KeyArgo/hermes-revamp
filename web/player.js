// player.js — Webamp bridge: init, saved-state restore/persist, command handling,
// heartbeat, ducking, resume-for-long-tracks, radio now-playing, mini card updates.
import Webamp from "/vendor/webamp.butterchurn-bundle.min.mjs";
import { normalizeItem, normalizeItems, legacyKind, legacyUrl } from "./items.js";
import { getFocus } from "./focus/adapter.js";
import {
  MAIN_VIS_MODES, mainVisPrefs, mainVisFalloff, mainVisPeakFalloff, mainVisModePatch,
} from "./main-vis.js";

const fmt = (s) => {
  if (!Number.isFinite(s) || s < 0) return "--:--";
  s = Math.floor(s);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

// What the Webamp backend can do; an item may withhold the ITEM_GATED ones (a stream can't seek).
// Mirrors revamp_core/playback.py WEBAMP_CAPS / ITEM_GATED.
const WEBAMP_CAPS = ["play", "pause", "toggle", "stop", "next", "prev", "seek", "volume", "shuffle", "repeat", "play_index", "eq"];
const ITEM_GATED = ["seek"];
const effectiveCaps = (caps, item) => (item ? caps.filter((c) => !ITEM_GATED.includes(c) || item.capabilities?.includes(c)) : [...caps]);

// Webamp EQ band ids (numeric keys of state.equalizer.sliders).
const BAND_KEYS = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000];

// Webamp 2.3.1's setCurrentTrack() is documented as taking a playlist index but
// actually takes a track id; translate through state.playlist.trackOrder.
function setTrackIndex(webamp, i) {
  const order = webamp.store?.getState?.().playlist?.trackOrder;
  const id = Array.isArray(order) ? order[Math.max(0, Math.min(i, order.length - 1))] : i;
  if (id !== undefined) webamp.setCurrentTrack(id);
}

// Insert items right after the current queue index (the "Play next" command).
function insertAfter(list, index, items) {
  const at = Math.max(0, Math.min(index + 1, list.length));
  return [...list.slice(0, at), ...items, ...list.slice(at)];
}

export async function initPlayer(ctx) {
  const { apiGet, apiPut, apiPost, authedUrl, toast: pageToast } = ctx;
  // In the Hermes mini dock the iframe's own toasts are invisible (docked off-screen),
  // so mirror them to the shell; it decides how to show them (bar toast/pet/notify).
  const toast = (text, opts) => {
    try {
      if (window.parent !== window) {
        window.parent.postMessage({ type: "revamp:toast", text: String(text), kind: opts?.kind }, "*");
      }
    } catch { /* parent gone: nothing to mirror */ }
    return pageToast(text, opts);
  };
  if (new URLSearchParams(location.search).get("debug") === "1") {
    setTimeout(() => { try { console.log("[revamp] store state", window.__webamp?.store?.getState?.()); } catch {} }, 3000);
  }

  // ---- saved state (graceful if server offline)
  let saved = {};
  try { saved = await apiGet("/api/state"); } catch { saved = {}; }
  saved.settings ||= {};
  const persist = debounce(persistNow, 1000);
  let persistState = {};
  function persistNow() {
    apiPut("/api/state", persistState).catch(() => {});
    persistState = {};
  }
  const stagePersist = (patch) => { Object.assign(persistState, patch); persist(); };

  // ---- load playlist items for restore (old saved shapes are upgraded here)
  let queueItems = normalizeItems(saved.playlist).filter(isWebampItem);

  // ---- queue entries: every track handed to Webamp carries "#rq<n>" at the end of its
  // URL, and `entries` maps n -> our item. Lookups go by that id, never by URL, so the
  // same URL twice stays two items and a swap in progress can't detach metadata
  // (an id from the old queue still finds its old item). The fragment never reaches
  // the server; audio elements ignore unknown fragments.
  const entries = new Map();
  let nextEntry = 1;
  let replacedAt = 1; // entries below this belong to a queue that was replaced
  let lastEntered = 0;
  const entryOf = (url) => { const m = /#rq(\d+)$/.exec(String(url || "")); return m ? Number(m[1]) : null; };
  const playUrl = (item) => {
    const u = item.playback.url;
    // Our own routes need the token; never send it to another host.
    return u.startsWith("/") ? authedUrl(u) : u;
  };
  const toWebampTrack = (item) => {
    const n = nextEntry++;
    entries.set(n, item);
    lastEntered = n;
    return {
      url: `${playUrl(item)}#rq${n}`,
      metaData: { title: item.title || undefined, artist: item.artist || "" },
      duration: item.duration ?? undefined,
    };
  };
  const initialTracks = queueItems.map(toWebampTrack);

  // ---- skins
  let skins = [];
  try { skins = await apiGet("/api/skins"); } catch { /* offline */ }
  const skinOptions = skins.filter((s) => s.url).map((s) => ({ url: location.origin + s.url, name: s.name }));
  const initialSkin = saved.skin && saved.skin !== "classic" && skins.find((s) => s.id === saved.skin)?.url
    ? location.origin + skins.find((s) => s.id === saved.skin).url : undefined;

  // ---- webamp options
  const options = {
    initialTracks: initialTracks.length ? initialTracks : undefined,
    availableSkins: skinOptions.length ? skinOptions : undefined,
    // initialSkin is applied after render instead: booting Webamp 2.3.1 with a
    // non-classic initialSkin inside the Revamp shell froze the page (see docs/NOTES.md).
    enableHotkeys: true,
    enableMediaSession: false, // ours below: knows radio from tracks (no seeking a live stream)
    // Webamp 2.x WindowLayout: classic stack — main, EQ under it, playlist under that.
    windowLayout: {
      main: { position: { top: 0, left: 0 } },
      equalizer: { position: { top: 116, left: 0 } },
      // Shorter playlist in the narrow/embed layout, where the panel below lists tracks anyway.
      playlist: { position: { top: 232, left: 0 }, size: { extraHeight: document.body.classList.contains("embed") ? 2 : 4, extraWidth: 0 } },
      // MilkDrop's closest 1x size step to the main window plus EQ is 275 × 232.
      // Its position changes when Hermes tells us which classic-player home is active.
      milkdrop: { position: { top: 0, left: 275 }, size: { extraHeight: 12, extraWidth: 0 } },
    },
    handleAddUrlEvent: () => openAddUrl(),
    handleLoadListEvent: () => window.revampSetView?.("playlists"),
    handleSaveListEvent: () => openSaveQueue(),
    filePickers: [{
      contextName: "Revamp Library…",
      filePicker: (options) => window.revampPickFiles?.(options) ?? Promise.resolve([]),
    }],
  };

  // Webamp plays through <audio> elements it makes itself and doesn't expose them. Keep a
  // weak list so playback speed (books) can be set on them. loadedmetadata is how we
  // learn a duration the library scan could not (no ffprobe); reported below.
  const audioEls = [];
  const origCreate = document.createElement;
  document.createElement = function (tag, ...rest) {
    const el = origCreate.call(this, tag, ...rest);
    if (String(tag).toLowerCase() === "audio") {
      audioEls.push(new WeakRef(el));
      const report = () => { try { reportLearnedDuration(); } catch { /* not ready yet */ } };
      el.addEventListener("loadedmetadata", report);
      el.addEventListener("durationchange", report);
    }
    return el;
  };
  const webamp = new Webamp(options);
  window.__webamp = webamp;
  // Before the first paint, so a saved oscilloscope / off choice does not flash the spectrum.
  alignMainVisMode(webamp, saved.settings);
  // renderInto keeps the windows inside the stage (renderWhenReady floats them over the page).
  await webamp.renderInto(document.getElementById("webamp-host"));
  if (initialSkin) { try { webamp.setSkinFromUrl(initialSkin); } catch { /* keep classic */ } }
  // Title-bar buttons, Winamp style. Left to Webamp, ✕ hides the whole player and minimize
  // does nothing visible here, so inside Hermes: minimize tucks the player into the bottom-bar
  // mini player and keeps playing; ✕ stops the music and does the same.
  if (window.parent !== window) {
    const toMini = () => window.parent.postMessage({ type: "revamp:location", location: "mini" }, "*");
    try {
      webamp.onWillClose((cancel) => {
        cancel();
        handleCommand({ action: "stop" });
        toMini();
      });
      webamp.onMinimize(() => {
        toMini();
      });
    } catch { /* older Webamp: its default behaviour */ }
  }
  if (saved.layout && typeof saved.layout === "object") {
    try { for (const [k, v] of Object.entries(saved.layout)) webamp.updateWindowLayout?.(k, v); } catch { /* optional */ }
  }
  if (saved.current_index > 0 && queueItems.length) {
    try { setTrackIndex(webamp, saved.current_index); } catch { /* guard */ }
  }
  try { webamp.setVolume(clamp(saved.volume ?? 75)); } catch { /* guard */ }
  if (typeof saved.balance === "number") dispatchStore({ type: "SET_BALANCE", balance: clamp(saved.balance, -100, 100) });
  if (saved.shuffle) try { webamp.toggleShuffle(); } catch { /* guard */ }
  if (saved.repeat) try { webamp.toggleRepeat(); } catch { /* guard */ }
  if (saved.eq) applySavedEq(saved.eq);
  // Moving between Hermes and the desktop window: carry on where the other player was.
  // Claimed atomically on the server, so only one starting player can act on it.
  const handoffFor = new URLSearchParams(location.search).has("float") ? "float" : "hermes";
  let handoffSeek = false; // the handoff's place is fresher than a book's saved one: skip that resume
  apiPost("/api/handoff/claim", { for: handoffFor }).then(({ handoff } = {}) => {
    if (!handoff?.playing || !queueItems.length) return;
    handoffSeek = true;
    setTimeout(() => { handoffSeek = false; }, 5000); // only the start it hands over
    const isStream = queueItems[saved.current_index || 0]?.kind === "stream";
    setTimeout(() => {
      try {
        webamp.play();
        if (!isStream && handoff.position > 1) setTimeout(() => { try { webamp.seekToTime(handoff.position); } catch { /* guard */ } }, 500);
      } catch { /* guard */ }
    }, 300);
  }).catch(() => {});

  function applySavedEq(eq) {
    const st = storeState();
    const sl = st?.equalizer?.sliders;
    if (!sl) return;
    if (typeof eq.preamp === "number") dispatchStore({ type: "SET_BAND_VALUE", band: "preamp", value: clamp(eq.preamp) });
    (eq.bands || []).forEach((v, i) => {
      const freqBand = BAND_KEYS[i];
      if (freqBand && typeof v === "number") dispatchStore({ type: "SET_BAND_VALUE", band: freqBand, value: clamp(v) });
    });
    if (eq.on) dispatchStore({ type: "SET_EQ_ON" }); else dispatchStore({ type: "SET_EQ_OFF" });
  }

  // ---- store helpers (all guarded)
  function store() { try { return webamp.store; } catch { return null; } }
  function storeState() { try { return store()?.getState(); } catch { return null; } }
  function dispatchStore(action) { try { store()?.dispatch(action); } catch { /* guard */ } }
  function media() { try { return storeState()?.media || {}; } catch { return {}; } }

  // ---- our items for Webamp's playlist, by entry id (see `entries` above)
  const untagged = new Map(); // Webamp track id -> item, for tracks Webamp added itself (drag and drop)
  function itemFor(t, webampId) {
    const n = entryOf(t?.url);
    if (n != null && entries.has(n)) return entries.get(n);
    if (webampId != null && untagged.has(webampId)) return untagged.get(webampId);
    const it = itemFromWebampTrack(t);
    if (it && webampId != null) untagged.set(webampId, it);
    return it;
  }
  function webampItems() {
    const tracks = safe(() => webamp.getPlaylistTracks?.()) || [];
    const order = storeState()?.playlist?.trackOrder || [];
    return tracks.map((t, i) => itemFor(t, order[i]));
  }
  // Webamp has taken the newest list we gave it (not mid-swap), so its order is current.
  function webampSettled() {
    if (!lastEntered) return true;
    return (safe(() => webamp.getPlaylistTracks?.()) || []).some((t) => entryOf(t.url) === lastEntered);
  }
  // Forget a replaced queue's entries once Webamp no longer lists any of them.
  function pruneEntries() {
    if (replacedAt <= 1 || !webampSettled()) return;
    const live = (safe(() => webamp.getPlaylistTracks?.()) || []).map((t) => entryOf(t.url));
    if (live.some((n) => n != null && n < replacedAt)) return;
    for (const n of entries.keys()) if (n < replacedAt) entries.delete(n);
  }
  function itemFromWebampTrack(t) {
    const u = stripToken(t?.url || "");
    const isStream = u.includes("/api/radio/stream");
    let inner = u;
    if (isStream) { try { inner = new URL(u, location.origin).searchParams.get("url") || u; } catch { /* keep */ } }
    // Webamp's PlaylistTrack has title/artist at the top level; never derive a title from a tokened URL.
    const title = t?.title || t?.metaData?.title || t?.defaultName || (isStream ? "Radio stream" : decodeURIComponent(inner.split("/").pop() || "Untitled"));
    return normalizeItem({ kind: isStream ? "stream" : "track", title, artist: t?.artist || t?.metaData?.artist || "", url: inner, duration: t?.duration ?? null });
  }
  function stripToken(u) {
    try { const x = new URL(u, location.origin); x.searchParams.delete("t"); x.hash = ""; return x.origin === location.origin ? x.pathname + x.search : x.toString(); }
    catch { return String(u || "").replace(/([?&])t=[0-9a-f]+&?/i, "$1").replace(/#.*$/, ""); }
  }
  // The queue as last saved: what a save falls back to while Webamp is mid-swap.
  let savedQueue = [...queueItems];

  // ---- other playback backends (focus, ...) register here; Webamp is built in.
  // executor: { load(items, {mode, play}), command(cmd) -> bool handled, status() -> {item, status, position, duration, capabilities} }
  const backends = new Map();
  let activeBackend = "webamp";
  const focusItem = () => ({
    id: "focus:session",
    source: "focus",
    kind: "focus",
    title: "Focus · deep drone",
    playback: { backend: "focus" },
    capabilities: ["play", "pause", "toggle", "stop", "volume"],
  });

  // ---- public-ish API used by views and commands
  const api = {
    webamp,
    get items() { return webampItems(); },
    async loadTracks(tracks, { mode = "replace", play = true } = {}) {
      const all = normalizeItems(tracks);
      // A list for another backend (focus...) goes to that backend; Webamp keeps its queue.
      const other = all[0] && all[0].playback.backend !== "webamp" ? all[0].playback.backend : null;
      if (other) {
        const ex = backends.get(other);
        if (!ex) { toast(`Can't play that here (${other})`, { kind: "error" }); return; }
        // Music may be playing even when another backend was last active (Webamp's own
        // play button doesn't go through here), so check Webamp itself.
        if (media().status === "PLAYING") try { webamp.pause(); } catch { /* guard */ }
        if (activeBackend !== "webamp" && activeBackend !== other) backends.get(activeBackend)?.command({ action: "stop" });
        activeBackend = other;
        try { await ex.load(all.filter((x) => x.playback.backend === other), { mode, play }); } catch (e) { console.warn("backend load failed", other, e); }
        return;
      }
      // Only stop the non-Webamp backend when this load actually starts Webamp
      // audio (replace+play): append/next are pure queue edits and must not
      // kill Focus or whatever else is playing.
      if (activeBackend !== "webamp" && !(mode === "replace" && play)) { /* queue edit: keep it playing */ }
      else if (activeBackend !== "webamp") { backends.get(activeBackend)?.command({ action: "stop" }); activeBackend = "webamp"; }
      const newItems = all.filter(isWebampItem);
      const currentIndex = currentInfo().index;
      let queue;
      if (mode === "replace") {
        queue = newItems;
        replacedAt = nextEntry; // the old queue's entries are dropped once Webamp lets go of them
        try { webamp.setTracksToPlay(newItems.map(toWebampTrack)); } catch { /* guard */ }
        if (play) try { webamp.play(); } catch { /* guard */ }
      } else {
        const base = webampSettled() ? webampItems() : savedQueue;
        const at = Math.max(0, Math.min(currentIndex + 1, base.length));
        queue = mode === "next" ? insertAfter(base, currentIndex, newItems) : [...base, ...newItems];
        try { webamp.appendTracks(newItems.map(toWebampTrack)); } catch { /* guard */ }
        if (mode === "next") {
          // Webamp's public API only appends: move the new tracks into place after
          // the current one, keeping the current track playing where it is.
          try {
            const order = storeState()?.playlist?.trackOrder || [];
            const added = order.slice(order.length - newItems.length);
            const rest = order.slice(0, order.length - newItems.length);
            dispatchStore({ type: "SET_TRACK_ORDER", trackOrder: [...rest.slice(0, at), ...added, ...rest.slice(at)] });
          } catch { /* guard */ }
        }
      }
      // Our own copy: Webamp may still list the old queue for a moment.
      savedQueue = queue;
      // Appending must not turn the current song back into the first entry on
      // the next reload.  Replacement intentionally starts at the first item.
      stagePersist({ playlist: [...queue], current_index: mode === "replace" ? 0 : currentIndex });
      updateNowPlayingSoon();
    },
    registerBackend(id, executor) { backends.set(id, executor); },
    get activeBackend() { return activeBackend; },
    async playIndex(i) {
      try { setTrackIndex(webamp, i); webamp.play(); } catch { /* guard */ }
    },
    async playItemsAt(index, list) { // play a specific item from a result set
      await this.loadTracks(list, { mode: "replace", play: false });
      this.playIndex(index);
    },
    stop() { try { webamp.stop(); } catch { /* guard */ } },
    // Webamp's pause() toggles: pausing something already paused would start it again.
    pause() { try { if (media().status === "PLAYING") webamp.pause(); } catch { /* guard */ } },
    play() { try { webamp.play(); } catch { /* guard */ } },
    toggle() {
      const st = media().status;
      try { st === "PLAYING" ? webamp.pause() : webamp.play(); } catch { /* guard */ }
    },
    next() { try { webamp.nextTrack(); } catch { /* guard */ } },
    prev() { try { webamp.previousTrack(); } catch { /* guard */ } },
    seek(sec) { try { webamp.seekToTime(sec); } catch { /* guard */ } },
    // ±seconds within the current track or book; a live stream has nowhere to seek.
    // Playback speed (0.5-3x, pitch kept): set on Webamp's audio elements. Load resets an
    // element's rate to its default, so both are set, and new tracks keep the speed.
    setRate(r) {
      const rate = Math.min(3, Math.max(0.5, Number(r) || 1));
      for (const ref of audioEls) {
        const el = ref.deref();
        if (!el) continue;
        try { el.preservesPitch = true; el.defaultPlaybackRate = rate; el.playbackRate = rate; } catch { /* guard */ }
      }
      this.rate = rate;
      return rate;
    },
    rate: 1,
    // Speed is remembered per book (by its resume key), with the last speed used as the default.
    bookRate(it) {
      const s = this.getSettings?.() || {};
      const key = it?.resumeKey || it?.id;
      return Number((s.bookRates || {})[key]) || Number(s.bookRate) || 1;
    },
    saveBookRate(it, rate) {
      const s = this.getSettings?.() || {};
      const key = it?.resumeKey || it?.id;
      const rates = { ...(s.bookRates || {}) };
      if (key) {
        delete rates[key];
        rates[key] = rate;
        const keys = Object.keys(rates);
        for (const k of keys.slice(0, Math.max(0, keys.length - 500))) delete rates[k]; // oldest first
      }
      this.setSavedSettings?.({ bookRates: rates, bookRate: rate });
    },
    localTid: (it) => localTid(it),
    seekBy(delta) {
      const info = currentInfo();
      if (!info.item || info.item.kind === "stream" || !Number.isFinite(Number(delta))) return;
      let to = Math.max(0, (info.position || 0) + Number(delta));
      if (info.duration > 0) to = Math.min(to, Math.max(0, info.duration - 1));
      try { webamp.seekToTime(to); } catch { /* guard */ }
      updateNowPlayingSoon();
    },
    setVolume(v) { try { webamp.setVolume(clamp(v)); } catch { /* guard */ } stagePersist({ volume: clamp(v) }); },
    setShuffle(on) {
      const cur = !!safe(() => webamp.isShuffleEnabled());
      if (cur !== !!on) try { webamp.toggleShuffle(); } catch { /* guard */ }
      stagePersist({ shuffle: !!on });
      setTimeout(beat, 50); // the Hermes mini player shows the toggle from the heartbeat
    },
    setRepeat(on) {
      const cur = !!safe(() => webamp.isRepeatEnabled());
      if (cur !== !!on) try { webamp.toggleRepeat(); } catch { /* guard */ }
      stagePersist({ repeat: !!on });
      setTimeout(beat, 50); // the Hermes mini player shows the toggle from the heartbeat
    },
    setEq(patch = {}) {
      if (typeof patch.on === "boolean") dispatchStore({ type: patch.on ? "SET_EQ_ON" : "SET_EQ_OFF" });
      if (typeof patch.preamp === "number") dispatchStore({ type: "SET_BAND_VALUE", band: "preamp", value: clamp(patch.preamp) });
      (patch.bands || []).forEach((v, i) => {
        if (typeof v === "number" && BAND_KEYS[i]) dispatchStore({ type: "SET_BAND_VALUE", band: BAND_KEYS[i], value: clamp(v) });
      });
      persistEq();
    },
    setSkin(id) {
      // A museum skin installed after this page loaded: refresh the list, then apply.
      if (/^museum:[0-9a-f]{32}$/.test(String(id)) && !skins.some((x) => x.id === id)) {
        apiGet("/api/skins").then((list) => {
          if (Array.isArray(list) && list.some((x) => x.id === id)) { skins = list; api.setSkin(id); }
        }).catch(() => {});
        return;
      }
      const want = String(id || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
      const s = skins.find((x) => x.id === id) ||
        skins.find((x) => [x.id, x.name, x.url || ""].some((v) => String(v).toLowerCase().replace(/[^a-z0-9]+/g, "").includes(want)));
      if (s) id = s.id;
      stagePersist({ skin: id });
      document.documentElement.classList.toggle("revamp-smooth-milkdrop", String(id).startsWith("builtin:REVamp-"));
      if (s?.url) { try { webamp.setSkinFromUrl(location.origin + s.url); } catch { /* guard */ } return; }
      if (!s && id !== "classic") return;
      // classic: base skin, simplest reliable path is a reload. Save first, or the
      // reload restores the old skin.
      const patch = persistState; persistState = {};
      apiPut("/api/state", patch).catch(() => {}).finally(() => location.reload());
    },
    skinList: () => skins,
    toast,
  };

  // Webamp's own skin menu calls this method directly, bypassing api.setSkin().
  // Remember a known skin URL so it is restored just like a skin chosen in REVamp.
  const setSkinFromUrl = webamp.setSkinFromUrl?.bind(webamp);
  if (setSkinFromUrl) {
    webamp.setSkinFromUrl = (url) => {
      const skin = skins.find((entry) => location.origin + entry.url === String(url));
      if (skin) stagePersist({ skin: skin.id });
      return setSkinFromUrl(url);
    };
  }

  // Music started from Webamp itself (its buttons, media keys) takes over from any other
  // backend, so the next Focus start knows music needs pausing.
  try {
    let lastStatus = media().status;
    webamp.store.subscribe(() => {
      const st = media().status;
      if (st === "PLAYING" && lastStatus !== "PLAYING" && activeBackend !== "webamp") {
        backends.get(activeBackend)?.command({ action: "stop" });
        activeBackend = "webamp";
        updateNowPlayingSoon();
      }
      lastStatus = st;
    });
  } catch { /* store shape changed: loadTracks still checks Webamp itself */ }

  // Focus is page-local generated audio, but otherwise obeys the same backend
  // contract as a remote player. Its status drives heartbeat and Hermes.
  const focus = getFocus((path, method, body) => {
    return method === "PUT" ? apiPut(path, body) : apiGet(path);
  });
  api.registerBackend("focus", {
    async load(items, { play }) {
      if (play) await focus.engine.start();
      else focus.pause();
    },
    command(cmd) {
      if (["focus_start", "focus_stop", "focus_toggle", "focus_set"].includes(cmd.action)) {
        if (cmd.action === "focus_start") api.loadTracks([focusItem()], { play: true });
        if (cmd.action === "focus_stop") focus.stop();
        if (cmd.action === "focus_toggle") focus.toggle();
        if (cmd.action === "focus_set") focus.set(cmd.params || {});
        updateNowPlayingSoon();
        return true;
      }
      if (!["play", "pause", "toggle", "stop", "volume"].includes(cmd.action)) return false;
      if (cmd.action === "play") focus.resume();
      if (cmd.action === "pause") focus.pause();
      if (cmd.action === "toggle") focus.toggle();
      if (cmd.action === "stop") focus.stop();
      if (cmd.action === "volume") focus.set({ volume: Number(cmd.percent) / 100 });
      updateNowPlayingSoon();
      return true;
    },
    status() {
      const s = focus.status();
      return {
        item: focusItem(),
        status: s.state === "playing" ? "PLAYING" : s.state === "paused" ? "PAUSED" : "STOPPED",
        position: s.elapsed,
        duration: null,
        capabilities: focusItem().capabilities,
      };
    },
  });
  // The full-page Focus view uses this rather than reaching into Webamp, so a
  // user click follows the same pause-and-activate route as an SSE command.
  window.revampFocusActivate = () => api.loadTracks([focusItem()], { play: true });
  focus.on(() => {
    updateNowPlayingSoon();
    setTimeout(beat, 0);
  });
  function persistEq() {
    const eq = storeState()?.equalizer;
    if (!eq?.sliders) return;
    stagePersist({ eq: { on: !!eq.on, preamp: eq.sliders.preamp ?? 50, bands: BAND_KEYS.map((b) => eq.sliders[b] ?? 50) } });
  }

  // ---- now playing card + heartbeat + resume + radio nowplaying
  let currentTrackId = null;
  let radioUrl = null;
  let radioSong = null; // ICY StreamTitle of the current station, when known
  let lastPosition = 0;

  function currentInfo() {
    if (activeBackend !== "webamp") {
      const st = safe(() => backends.get(activeBackend)?.status?.()) || {};
      return { item: st.item || null, index: 0, webampTrack: null, status: st.status || "STOPPED",
        position: st.position ?? 0, duration: st.duration ?? null, capabilities: st.capabilities };
    }
    const state = storeState();
    const pl = state?.playlist;
    const idx = pl && Array.isArray(pl.trackOrder) ? Math.max(0, pl.trackOrder.indexOf(pl.currentTrack)) : 0;
    const wid = pl?.trackOrder?.[idx];
    // By Webamp's track id -> our entry id: never by position or URL.
    const t = state?.tracks?.[wid] || safe(() => webamp.getPlaylistTracks?.()?.[idx]);
    const item = t ? itemFor(t, wid) : null;
    const m = media();
    return {
      item, index: idx, webampTrack: t,
      status: m.status || "STOPPED",
      position: m.timeElapsed ?? 0,
      duration: m.length ?? item?.duration ?? null,
      volume: m.volume, balance: m.balance,
    };
  }

  // A local library track's id: core items say "local:track:<tid>" (track_id is legacy).
  const localTid = (it) => (typeof it?.id === "string" && it.id.startsWith("local:track:"))
    ? it.id.slice("local:track:".length) : (it?.track_id || null);

  function updateNowPlaying() {
    const info = currentInfo();
    const it = info.item;
    const title = it?.title || safe(() => info.webampTrack?.metaData?.title) || "Nothing playing";
    const isStream = it?.kind === "stream";
    const artist = (isStream && radioSong) || it?.artist || safe(() => info.webampTrack?.metaData?.artist) || "";
    // Core items carry their own art (any source); older local items fall back to the track id.
    const art = isStream ? "" : it?.art ? (it.art.startsWith("/") ? authedUrl(it.art) : it.art)
      : localTid(it) ? authedUrl(`/api/art/${localTid(it)}`) : "";
    ctx.setNowPlaying({
      title, artist, isStream,
      time: isStream ? elapsed() : `${fmt(info.position)} / ${fmt(info.duration)}`,
      art,
    });
    lastPosition = info.position;
    paintNpExtras(it);
    try { api.onNow?.(it, info); } catch { /* extras are optional */ }
    updateMediaSession(info, title, artist, art);
  }
  const elapsed = () => `${fmt(lastPosition)}`;
  function updateNowPlayingSoon() { setTimeout(updateNowPlaying, 150); }

  // ---- books remember where you stopped (saved on the server, so any player resumes it)
  const BOOK_SAVE_MS = 10_000;
  let bookSaved = { id: null, at: 0, pos: -1 };
  let saveChain = Promise.resolve();
  let resumedKey = null;
  let resumingId = null; // until the seek lands, 0:01 must not overwrite the saved place
  // An item remembers its place when it has the "resume" capability (books); the place
  // is saved under its resumeKey, so a book from any source resumes the same way.
  const resumeKeyOf = (it) => (it?.capabilities?.includes("resume") && it.resumeKey) || null;
  function saveBookPlace(force = false) {
    if (activeBackend !== "webamp") return;
    const info = currentInfo();
    const key = resumeKeyOf(info.item);
    if (!key || resumingId === key || !(info.position > 0)) return;
    const pos = Math.floor(info.position);
    const finished = info.duration > 0 && pos >= info.duration - 30; // done: start over next time
    const value = finished ? 0 : pos;
    const now = Date.now();
    if (bookSaved.id === key && bookSaved.pos === value) return;
    if (!force && !finished && bookSaved.id === key && now - bookSaved.at < BOOK_SAVE_MS) return;
    bookSaved = { id: key, at: now, pos: value };
    const body = { key, position: finished ? null : pos };
    // One at a time, so an older place never lands after a newer one.
    saveChain = saveChain.then(() => apiPost("/api/resume", body)).catch(() => {});
  }
  async function maybeResume() {
    if (activeBackend !== "webamp") return;
    const info = currentInfo();
    const rk = resumeKeyOf(info.item);
    if (info.status !== "PLAYING" || !rk) return;
    const key = `${storeState()?.playlist?.currentTrack}|${rk}`;
    if (key === resumedKey) return;
    resumedKey = key;
    if (handoffSeek) { handoffSeek = false; return; }
    resumingId = rk;
    try {
      const res = await apiGet("/api/resume?key=" + encodeURIComponent(rk));
      const pos = Number(res?.position) || 0;
      if (pos <= 30) return;
      // Seek once the audio has actually started, or the seek is lost.
      for (let i = 0; i < 50 && !(media().timeElapsed > 0); i++) await new Promise((r) => setTimeout(r, 100));
      const now = currentInfo();
      const dur = now.duration || 0;
      if (resumeKeyOf(now.item) !== rk) return;
      if ((dur && pos >= dur - 30) || Math.abs((media().timeElapsed || 0) - pos) < 5) return;
      webamp.seekToTime(pos);
      toast(`Resumed at ${fmt(pos)}`, { kind: "ok" });
    } catch { /* play from the start */ } finally {
      if (resumingId === rk) resumingId = null;
    }
  }
  // Once <audio> or Webamp knows the real length, tell the library so the next
  // listing is not "--:--". One report per track until the length changes.
  const reportedDuration = new Map();
  function reportLearnedDuration() {
    const info = currentInfo();
    const it = info.item;
    const tid = localTid(it);
    if (!tid || it?.kind === "stream" || it?.kind === "focus") return;
    const dur = Number(info.duration);
    if (!Number.isFinite(dur) || dur <= 0 || dur > 48 * 3600) return;
    const known = Number(it.duration);
    if (Number.isFinite(known) && known > 0 && Math.abs(known - dur) < 0.5) return;
    const rounded = Math.round(dur * 1000) / 1000;
    if (reportedDuration.get(tid) === rounded) return;
    reportedDuration.set(tid, rounded);
    apiPost("/api/library/duration", { id: tid, duration: rounded }).then(() => {
      it.duration = rounded;
    }).catch(() => { reportedDuration.delete(tid); });
  }
  function beat() {
    const info = currentInfo();
    const it = info.item;
    const backend = activeBackend;
    const caps = info.capabilities || effectiveCaps(WEBAMP_CAPS, it);
    apiPost("/api/heartbeat", {
      now_playing: {
        // The old fields (kind is track|stream) keep older status bars working.
        title: it?.title ?? null, artist: (it?.kind === "stream" && radioSong) || it?.artist || null,
        kind: it ? legacyKind(it) : null, url: it ? legacyUrl(it) : null,
        position: Math.floor(info.position), duration: info.duration, status: info.status,
        // What state.playback is built from (see revamp_core/store.py).
        item_id: it?.id ?? null, source: it?.source ?? null, media_kind: it?.kind ?? null,
        backend, resume_key: resumeKeyOf(it), capabilities: caps,
        // For the Hermes mini player: queue position, toggles, sleep timer, the heart.
        index: info.index, track_id: localTid(it),
        shuffle: !!safe(() => webamp.isShuffleEnabled()), repeat: !!safe(() => webamp.isRepeatEnabled()),
        sleep: sleepStatus(), rate: api.rate,
      },
    }).catch(() => {});
  }
  // heartbeat (2 s) — fire and forget
  setInterval(() => {
    updateNowPlaying();
    beat();
    reportLearnedDuration();
    pruneEntries();
    saveBookPlace();
    persistNow(); // flush eq patches at heartbeat cadence too
  }, 2000);
  // ...and at once on play/pause/stop or a track change, so the Hermes status
  // bar (which reads the server) doesn't lag a click by up to 2 s.
  let lastBeatKey = "";
  let lastOrderKey = "";
  let lastCurrent = null;
  let lastNativeSettings = "";
  function persistNativeSettings(st) {
    const equalizer = st?.equalizer;
    const sliders = equalizer?.sliders;
    const patch = {
      balance: Number.isFinite(st?.media?.balance) ? clamp(st.media.balance, -100, 100) : undefined,
      shuffle: !!safe(() => webamp.isShuffleEnabled()),
      repeat: !!safe(() => webamp.isRepeatEnabled()),
    };
    if (!duck.active && Number.isFinite(st?.media?.volume)) patch.volume = clamp(st.media.volume);
    if (sliders) {
      patch.eq = {
        on: !!equalizer.on,
        preamp: sliders.preamp ?? 50,
        bands: BAND_KEYS.map((band) => sliders[band] ?? 50),
      };
    }
    const key = JSON.stringify(patch);
    if (key === lastNativeSettings) return;
    lastNativeSettings = key;
    stagePersist(patch);
  }
  // The queue position and any drag-reorder in Webamp's playlist window are saved as they
  // change, so a reload, a restart or the desktop pop-out carries on from the right item.
  function saveQueuePosition(orderChanged) {
    if (activeBackend !== "webamp" || !webampSettled()) return;
    const pl = storeState()?.playlist;
    if (!pl || !Array.isArray(pl.trackOrder)) return;
    const index = Math.max(0, pl.trackOrder.indexOf(pl.currentTrack));
    if (orderChanged) {
      savedQueue = webampItems();
      stagePersist({ playlist: [...savedQueue], current_index: index });
    } else {
      stagePersist({ current_index: index });
    }
  }
  try {
    webamp.store.subscribe(() => {
      const st = storeState();
      persistNativeSettings(st);
      const orderKey = (st?.playlist?.trackOrder || []).join(",");
      const current = st?.playlist?.currentTrack ?? null;
      if (orderKey !== lastOrderKey || current !== lastCurrent) {
        const orderChanged = orderKey !== lastOrderKey && lastOrderKey !== "";
        lastOrderKey = orderKey;
        lastCurrent = current;
        setTimeout(() => saveQueuePosition(orderChanged), 0); // off Webamp's dispatch
      }
      const key = `${st?.media?.status}|${st?.playlist?.currentTrack}`;
      if (key === lastBeatKey) return;
      lastBeatKey = key;
      setTimeout(beat, 50);
      // After this store update, not inside it: mid-update Webamp can still list the old
      // queue. (Items map by entry id now, but the deferral keeps reads off Webamp's dispatch.)
      setTimeout(() => { saveBookPlace(true); maybeResume(); }, 0); // paused/stopped: keep the exact place
    });
  } catch { /* store shape changed: the 2 s heartbeat still runs */ }

  // ---- MilkDrop visualizer: the floating pop-out has an intentionally separate
  // remembered choice. It starts closed there, while the page and docked pane start open.
  // The desktop window (?float=1) has its own choice too: the OS window is sized to the
  // player alone until MilkDrop is turned on, then to the player with MilkDrop beside it.
  const desktopFloat = new URLSearchParams(location.search).has("float");
  let visualizerPlacement = desktopFloat
    ? "desktop"
    : document.body.classList.contains("mode-winamp") ? "floating" : "page";
  let milkdropSuspended = false;
  let resumeMilkdropOnVisible = false;
  const isMiniVisible = () => !!document.querySelector(".revamp-mini:not([hidden])");
  const milkdropSetting = () => {
    if (visualizerPlacement === "docked") return "milkdropOpenDocked";
    if (visualizerPlacement === "floating") return "milkdropOpenFloating";
    if (visualizerPlacement === "desktop") return "milkdropOpenDesktop";
    return "milkdropOpen";
  };
  const wantsMilkdropOpen = () => {
    const key = milkdropSetting();
    if (Object.prototype.hasOwnProperty.call(saved.settings, key)) return saved.settings[key] === true;
    // Off by default in Hermes's floating overlay and the desktop window, and
    // anywhere graphics are drawn by the CPU.
    const optional = visualizerPlacement === "floating" || visualizerPlacement === "desktop";
    return !optional && !(hasWebGL() && softwareGL);
  };
  function milkdropPosition() {
    const host = document.getElementById("webamp-host")?.getBoundingClientRect();
    const main = document.getElementById("main-window")?.getBoundingClientRect();
    const playlist = document.getElementById("playlist-window")?.getBoundingClientRect();
    if (!host || !main || !playlist) {
      if (visualizerPlacement === "docked") return { x: 0, y: 348 };
      return { x: 275, y: 0 };
    }
    if (visualizerPlacement === "docked") {
      return { x: Math.round(main.left - host.left), y: Math.round(playlist.bottom - host.top) };
    }
    return { x: Math.round(main.right - host.left), y: Math.round(main.top - host.top) };
  }

  // MilkDrop matches the classic stack: beside it, as tall as main + EQ + playlist (measured);
  // below it (docked), as tall as main + EQ (275 x 232). Webamp sizes generic windows in steps of
  // 25 x 29 px from 275 x 116; app.css reads --milkdrop-h for the frame around the canvas.
  function placeMilkdrop() {
    const position = milkdropPosition();
    const top = document.getElementById("main-window")?.getBoundingClientRect();
    const bottom = document.getElementById("playlist-window")?.getBoundingClientRect();
    const stackH = top && bottom ? bottom.bottom - top.top : 464;
    const rows = visualizerPlacement === "docked" ? 4 : Math.max(4, Math.round((stackH - 116) / 29));
    // The desktop window has no page around the player, so the frame is the
    // stack's exact height. A 29px step would leave a strip beside it.
    const frameH = visualizerPlacement === "desktop" ? Math.max(116, Math.round(stackH)) : 116 + rows * 29;
    document.documentElement.style.setProperty("--milkdrop-h", `${frameH}px`);
    try {
      webamp.store.dispatch({
        type: "UPDATE_WINDOW_POSITIONS",
        absolute: true,
        positions: { milkdrop: position },
      });
      webamp.store.dispatch({ type: "WINDOW_SIZE_CHANGED", windowId: "milkdrop", size: [0, rows] });
    } catch { /* old bundle without MilkDrop layout actions */ }
  }

  // A page-sized stage can leave the classic stack at a page-relative position.
  // The docked pane and floating overlay clip the iframe to this stack, so it
  // must start at the iframe origin whenever either Winamp home is selected.
  function placeClassicStack() {
    if (visualizerPlacement !== "docked" && visualizerPlacement !== "floating" && visualizerPlacement !== "desktop") return;
    // Stack from the measured heights in every home, so a closed EQ (or a shaded
    // main window, or a taller playlist) closes the gap instead of leaving the
    // playlist at a hardcoded y=232 with 116px of empty page above it.
    let eqY = 116;
    let plY = 232;
    const mainH = Math.round(document.getElementById("main-window")?.getBoundingClientRect().height || 0);
    const eqNode = document.getElementById("equalizer-window");
    const eqShown = eqNode && getComputedStyle(eqNode).display !== "none" && getComputedStyle(eqNode).visibility !== "hidden";
    const eqH = eqShown ? Math.round(eqNode.getBoundingClientRect().height || 0) : 0;
    if (mainH > 20) eqY = mainH;
    if (mainH > 20) plY = mainH + (eqH > 20 ? eqH : 0);
    try {
      webamp.store.dispatch({
        type: "UPDATE_WINDOW_POSITIONS",
        absolute: true,
        positions: {
          main: { x: 0, y: 0 },
          equalizer: { x: 0, y: eqY },
          playlist: { x: 0, y: plY },
        },
      });
    } catch { /* old bundle without classic layout actions */ }
  }

  // The desktop window shows the whole classic stack. A closed EQ or playlist
  // would size the OS window to a partial player.
  function ensureClassicOpen() {
    for (const id of ["equalizer", "playlist"]) {
      try {
        const wins = webamp.store.getState().windows;
        const node = wins?.genWindows?.[id] ?? wins?.[id];
        if (node && node.open === false) webamp.store.dispatch({ type: "TOGGLE_WINDOW", windowId: id });
      } catch { /* stay as Webamp left it */ }
    }
  }

  api.layoutDesktop = () => {
    if (visualizerPlacement !== "desktop") return false;
    ensureClassicOpen();
    placeClassicStack();
    placeMilkdrop();
    return true;
  };

  function desktopLayoutChanged() {
    if (visualizerPlacement !== "desktop") return;
    api.layoutDesktop();
    window.dispatchEvent(new CustomEvent("revamp:desktop-layout"));
  }

  // Open state follows Webamp's store (including its close button) and is remembered
  // for the current home, so moving the same iframe does not overwrite the other choice.
  function milkdropOpen() {
    try { return !!webamp.store.getState().windows.genWindows.milkdrop?.open; } catch { return false; }
  }
  let lastMilkdrop = milkdropOpen();
  try {
    webamp.store.subscribe(() => {
      const open = milkdropOpen();
      if (open === lastMilkdrop) return;
      lastMilkdrop = open;
      if (!milkdropSuspended) api.setSavedSettings?.({ [milkdropSetting()]: open });
      paintNpExtras();
      // Off Webamp's dispatch: pin the stack and ask the desktop window to refit.
      if (visualizerPlacement === "desktop") setTimeout(desktopLayoutChanged, 0);
    });
  } catch { /* store shape changed: the button still toggles */ }

  // A closed EQ unmounts its window, so the playlist would stay where the EQ
  // used to be — 116px of empty page between the two windows. Watch the EQ's
  // open state in the compact homes and re-place the stack when it changes.
  // (The desktop pop-out keeps its EQ open on purpose; ensureClassicOpen owns it.)
  function eqOpen() {
    try { return !!webamp.store.getState().windows.genWindows.equalizer?.open; } catch { return false; }
  }
  let lastEq = eqOpen();
  try {
    webamp.store.subscribe(() => {
      const open = eqOpen();
      if (open === lastEq) return;
      lastEq = open;
      if (visualizerPlacement === "desktop") return;
      // Off Webamp's own layout pass: let it hide the window first.
      setTimeout(() => {
        placeClassicStack();
        window.dispatchEvent(new CustomEvent("revamp:desktop-layout"));
      }, 0);
    });
  } catch { /* store shape changed: the EQ button still toggles */ }

  // Live streams report an infinite duration, which Webamp's time formatter
  // prints as "InfinityNaN:NaNNaN"; null makes it show no length instead.
  try {
    webamp.store.subscribe(() => {
      const tracks = webamp.store.getState().tracks || {};
      for (const id of Object.keys(tracks)) {
        const duration = tracks[id]?.duration;
        if (typeof duration === "number" && !Number.isFinite(duration)) {
          webamp.store.dispatch({ type: "SET_MEDIA_DURATION", id, duration: null });
        }
      }
    });
  } catch { /* store shape changed: lengths still come from the metadata */ }

  // WebGL is what Butterchurn renders with; without it we say so instead of a blank window.
  // Probed once: a WebGL context is scarce (browsers cap them), so the probe is cached and
  // its context released right away — repeated open/close cycles must not exhaust the pool.
  let webglProbe = null;
  let softwareGL = false;
  function hasWebGL() {
    if (webglProbe !== null) return webglProbe;
    try {
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
      webglProbe = !!gl;
      // Software WebGL (SwiftShader / llvmpipe): every frame is drawn by the CPU. Hermes falls
      // back to it on many NVIDIA Linux setups, where MilkDrop alone costs about half a core.
      const info = gl?.getExtension("WEBGL_debug_renderer_info");
      const renderer = String((info && gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) || "");
      softwareGL = /swiftshader|llvmpipe|softpipe|software/i.test(renderer);
      gl?.getExtension("WEBGL_lose_context")?.loseContext();
    } catch { webglProbe = false; }
    return webglProbe;
  }
  api.visualizerOpen = () => milkdropOpen();
  api.toggleVisualizer = (open) => {
  // Mini-only has no visible surface for the window: toggling there would edit
  // remembered state invisibly (off-screen dock or page coords). Ask first.
  if (visualizerPlacement === "mini" && !isMiniVisible()) {
    toast("Switch out of Mini first to show the visualizer here.", { kind: "info" });
    return false;
  }
    const want = typeof open === "boolean" ? open : !milkdropOpen();
    if (want === lastMilkdrop) return lastMilkdrop; // double-click: no second dispatch, no race
    if (want && !hasWebGL()) {
      toast("The visualizer needs graphics acceleration (WebGL), which isn't available here.", { kind: "error" });
      return false;
    }
    try {
      if (!!milkdropOpen() !== want) webamp.store.dispatch({ type: "TOGGLE_WINDOW", windowId: "milkdrop" });
    } catch { /* old bundle without the window: stay closed */ }
    lastMilkdrop = milkdropOpen();
    paintNpExtras();
    return lastMilkdrop;
  };
  function suspendMilkdrop() {
    if (milkdropSuspended || !milkdropOpen()) return;
    milkdropSuspended = true;
    resumeMilkdropOnVisible = true;
    api.toggleVisualizer(false);
  }
  function resumeMilkdrop() {
    if (!milkdropSuspended) return;
    const resume = resumeMilkdropOnVisible;
    milkdropSuspended = false;
    resumeMilkdropOnVisible = false;
    if (resume && hasWebGL()) api.toggleVisualizer(true);
  }
  function syncMilkdropVisibility() {
    // Out of sight (mini player only, or a hidden tab) MilkDrop is closed, so it stops drawing;
    // it comes back when the player is visible again. The saved choice is not touched.
    const offscreen = visualizerPlacement === "mini" || document.hidden || hostSaysVisible === false;
    if (offscreen) suspendMilkdrop();
    else resumeMilkdrop();
  }
  api.setVisualizerPlacement = (placement) => {
    visualizerPlacement = placement === "docked" || placement === "floating" || placement === "mini" || placement === "desktop" ? placement : "page";
    syncMilkdropVisibility();
    if (visualizerPlacement === "mini") return;
    const applyPlacement = () => {
      placeClassicStack();
      placeMilkdrop();
      const wantOpen = hasWebGL() && wantsMilkdropOpen();
      if (api.visualizerOpen() !== wantOpen) api.toggleVisualizer(wantOpen);
    };
    applyPlacement();
    requestAnimationFrame(applyPlacement);
    // Webamp centres its initial classic-window layout asynchronously. Apply the
    // compact homes once more after that pass, otherwise the floating iframe
    // clips the title bar and the visualizer can remain outside its frame.
    setTimeout(applyPlacement, 250);
  };
  document.addEventListener("visibilitychange", syncMilkdropVisibility);
  let hostSaysVisible = true;
  window.addEventListener("message", (e) => {
    if ((window.parent !== window && e.source !== window.parent) || e.data?.type !== "revamp:visible") return;
    hostSaysVisible = e.data.visible !== false;
    syncMilkdropVisibility();
  });
  api.visualizerMetrics = () => ({
    running: milkdropOpen() && !milkdropSuspended && !document.hidden && hostSaysVisible && visualizerPlacement !== "mini",
    // Webamp unmounts MilkDrop's canvas when its window closes: no canvas, no drawing.
    canvas: Boolean(document.querySelector("#webamp .gen-window canvas, #milkdrop-window canvas")),
    status: safe(() => webamp.store.getState().media.status, ""),
  });
  api.fullscreenVisualizer = () => {
    const win = document.querySelector("#milkdrop-window, .gen-window");
    if (!win) return false;
    if (document.fullscreenElement) {
      document.exitFullscreen?.();
      return true;
    }
    win.requestFullscreen?.().catch(() => {});
    return true;
  };
  document.addEventListener("dblclick", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target?.closest("#milkdrop-window, .gen-window")) return;
    event.preventDefault();
    api.fullscreenVisualizer();
  }, true);

  // The built-in base skin has no title bar image in the store (its sprites are
  // baked into Webamp's CSS); only then does app.css swap in the title bar that
  // reads REVAMP. Any loaded skin (menu, drag and drop, the Skins view) clears it.
  const markClassic = () => {
    const imgs = storeState()?.display?.skinImages || {};
    document.documentElement.classList.toggle("revamp-classic", !imgs.MAIN_TITLE_BAR && !imgs.MAIN_TITLE_BAR_SELECTED);
    // A new skin builds a new renderer after this dispatch. Apply once it is committed.
    requestAnimationFrame(applyMainVis);
  };
  markClassic();
  // Webamp's spectrum canvas used to redraw every frame even when nothing played. Under
  // software graphics that was ~1.2 CPU cores, so the canvas is hidden while not playing.
  // Pause stops Webamp's loop but leaves the last bars in the bitmap; clear those so a
  // paused or stopped analyzer rests flat instead of frozen. The loop itself only runs
  // while status is PLAYING (Webamp's own effect), and only the small analyzer — not MilkDrop.
  const visCanvases = () => document.querySelectorAll("#webamp canvas#visualizer");
  function applyMainVis() {
    const prefs = mainVisPrefs(saved.settings);
    const bandwidth = prefs.bandwidth === "wide" ? "wide" : "thin";
    const falloff = mainVisFalloff(prefs);
    const peakFalloff = mainVisPeakFalloff(prefs);
    for (const canvas of visCanvases()) {
      const inst = visInstance(canvas);
      if (!inst?._vis) continue;
      inst._vis.bandwidth = bandwidth;
      inst._vis.peaks = prefs.peaks;
      inst._vis.saFalloff = prefs.saFalloff;
      inst._vis.saPeakFalloff = prefs.saPeakFalloff;
      inst.falloff = falloff;
      inst.peakFalloff = peakFalloff;
      canvas.dataset.revampVis = `${bandwidth}:${prefs.peaks ? "peaks" : "flat"}:${prefs.saFalloff}`;
    }
  }
  function flattenVis() {
    let status = "STOPPED";
    try { status = media().status; } catch { /* store not ready */ }
    if (status === "PLAYING") return;
    for (const canvas of visCanvases()) {
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }
  let visWasIdle = true;
  const markIdle = () => {
    let status = "STOPPED";
    try { status = media().status; } catch { /* store not ready */ }
    document.body.classList.toggle("revamp-idle", status !== "PLAYING");
    const idle = status !== "PLAYING";
    if (idle === visWasIdle) return;
    visWasIdle = idle;
    if (idle) {
      flattenVis();
      // The in-flight frame can repaint the old bars before Webamp cancels it.
      requestAnimationFrame(flattenVis);
      setTimeout(flattenVis, 40);
    } else {
      requestAnimationFrame(applyMainVis);
    }
  };
  markIdle();
  try { webamp.store.subscribe(markIdle); } catch { /* keep it visible */ }
  let visStyle = 0;
  try { visStyle = webamp.store.getState().display.visualizerStyle ?? 0; } catch { /* store not ready */ }
  try {
    webamp.store.subscribe(() => {
      let style = visStyle;
      try { style = webamp.store.getState().display.visualizerStyle ?? visStyle; } catch { return; }
      if (style === visStyle) return;
      visStyle = style;
      // Click (and only a real change) merges the mode into settings. Other keys stay.
      const patch = mainVisModePatch(saved.settings, MAIN_VIS_MODES[style] || "BAR");
      if (patch) api.setSavedSettings?.(patch);
      // The click builds a new renderer during React's commit, after this dispatch.
      requestAnimationFrame(applyMainVis);
      setTimeout(applyMainVis, 0);
    });
  } catch { /* a click still cycles; it just is not remembered */ }
  const visHost = document.getElementById("webamp-host");
  if (visHost && typeof MutationObserver === "function") {
    const mo = new MutationObserver(() => applyMainVis());
    mo.observe(visHost, { childList: true, subtree: true });
  }
  applyMainVis();
  try { webamp.store.subscribe(markClassic); } catch { /* keep the original title */ }
  document.documentElement.classList.toggle("revamp-smooth-milkdrop", String(saved.skin || "").startsWith("builtin:REVamp-"));

  try {
    webamp.onTrackDidChange?.(() => { updateNowPlayingSoon(); checkRadio(); });
  } catch { /* optional API */ }

  // radio now-playing: poll while a stream is the current track
  let radioTimer = null;
  let radioPoll = () => {};
  function checkRadio() {
    const it = currentInfo().item;
    const url = it?.kind === "stream" ? it.playback?.stream || null : null; // the station's own URL
    if (url === radioUrl) return;
    radioUrl = url;
    radioSong = null;
    clearInterval(radioTimer);
    if (!url) return;
    const poll = async () => {
      try {
        const np = await apiGet("/api/radio/nowplaying?url=" + encodeURIComponent(url));
        if (np?.title) {
          radioSong = np.title;
          updateNowPlaying();
          dispatchStore({ // id is Webamp's track id, not the playlist index
            type: "SET_MEDIA_TAGS", id: storeState()?.playlist?.currentTrack, title: it.title, artist: np.title,
            album: null, sampleRate: null, bitrate: null, numberOfChannels: null,
          });
        }
      } catch { /* network */ }
    };
    radioPoll = poll;
    poll();
    radioTimer = setInterval(() => { if (!document.hidden) poll(); }, 15000);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && radioUrl) radioPoll(); });

  // A short audible acknowledgement for a new, actionable approval.  It is
  // deliberately not tied to the approval lifecycle: waiting to be answered
  // must never leave the player quiet.
  let duck = { active: false, timer: null, savedVol: null, evId: null, sessionId: null };
  const seenApprovalIds = new Set();
  const APPROVAL_ALERT_MAX_AGE_MS = 10_000;
  function playApprovalChime() {
    if (saved.settings.duck_chime === false || currentVolume() <= 0 || media().muted) return;
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) return;
      const audio = new Audio();
      const gain = audio.createGain();
      // Scale this notification with the player's current output level.
      gain.gain.value = Math.min(0.09, Math.max(0.015, currentVolume() / 1000));
      gain.connect(audio.destination);
      const now = audio.currentTime;
      [660, 880].forEach((frequency, i) => {
        const tone = audio.createOscillator();
        tone.type = "sine";
        tone.frequency.value = frequency;
        tone.connect(gain);
        tone.start(now + i * 0.12);
        tone.stop(now + i * 0.12 + 0.11);
      });
    } catch { /* Audio is optional and may be blocked until user interaction. */ }
  }
  const dipDuration = () => Math.round(Math.min(5, Math.max(1, Number(saved.settings.duck_dip_seconds ?? 2))) * 1000);
  function duckTo(levelPct, reason = "", evId = null, sessionId = null) {
    if (duck.active) return;
    const m = media();
    // Nothing to turn down (paused, stopped, nothing loaded): no dip and no pop-up.
    if (String(m.status || "").toUpperCase() !== "PLAYING") return;
    if (saved.settings.duck_toast !== false) toast(`Music turned down${reason ? ": " + reason : ""}`, {});
    duck.savedVol = typeof m.volume === "number" ? m.volume : safe(() => webamp.getVolume?.()) ?? 75;
    duck.active = true;
    duck.evId = evId;
    duck.sessionId = sessionId;
    animateVolume(duck.savedVol, clamp(levelPct), 400, false);
    playApprovalChime();
    duck.timer = setTimeout(unduck, dipDuration());
  }
  function unduck(whoseId = null, sessionId = null) {
    // Hook events get separate event ids. Pair their lifecycle by Hermes session
    // so denial, timeout, and session-end restore the correct dip as well.
    if (!duck.active || (sessionId && duck.sessionId && sessionId !== duck.sessionId) || (!sessionId && whoseId && duck.evId && whoseId !== duck.evId)) return;
    clearTimeout(duck.timer);
    if (duck.savedVol != null) animateVolume(clamp(currentVolume()), duck.savedVol, 400, false);
    duck.active = false;
    duck.evId = null;
    duck.sessionId = null;
    if (saved.settings.duck_toast !== false) toast("Music back to normal volume", { kind: "ok" });
  }
  function currentVolume() { const v = media().volume; return typeof v === "number" ? v : safe(() => webamp.getVolume?.()) ?? 75; }
  function animateVolume(from, to, ms, persist = true) {
    const t0 = performance.now();
    const step = (t) => {
      const k = Math.min(1, (t - t0) / ms);
      const v = Math.round(from + (to - from) * k);
      try { webamp.setVolume(v); } catch { /* guard */ }
      if (k < 1) setTimeout(() => step(performance.now()), 16);
      else if (persist) stagePersist({ volume: to });
    };
    // setTimeout, not requestAnimationFrame: a hidden player gets no animation frames,
    // and the volume would stay stuck part-way down.
    setTimeout(() => step(performance.now()), 0);
  }
  const duckEnabled = () => saved.settings.duck_on_approval === true;
  // Default to this Desktop's local CLI prompt. Gateway and transport:* are
  // bots, cron, and background work unless the user explicitly chooses All.
  const approvalScope = () => saved.settings.duck_approval_scope === "all" ? "all" : "app";
  const approvalSession = (ev) => String(ev?.session_id || ev?.session_key || "");
  const approvalIsActionable = (ev) => ev?.source === "hermes" &&
    (approvalScope() === "all" || ev?.approval_surface === "cli");
  const duckFor = (ev) => duckEnabled() && approvalIsActionable(ev);
  const duckReason = (ev) => {
    const session = approvalSession(ev);
    const where = session ? ` (session ${session.slice(0, 12)})` : "";
    return "Hermes is waiting for your approval" + where + (ev?.preview ? `: ${String(ev.preview).slice(0, 60)}` : "");
  };
  const duckLevel = () => Math.round(currentVolume() * (saved.settings.duck_level ?? 0.25));
  const approvalIsFresh = (ev) => {
    const ts = Number(ev?.ts);
    return Number.isFinite(ts) && Math.abs(Date.now() - (ts > 1e12 ? ts : ts * 1000)) <= APPROVAL_ALERT_MAX_AGE_MS;
  };
  const alertFor = (ev) => {
    const id = String(ev?.id || "");
    if (!id || seenApprovalIds.has(id) || !approvalIsFresh(ev) || !duckFor(ev)) return false;
    seenApprovalIds.add(id);
    // Keep the small replay guard bounded on long-lived desktop sessions.
    if (seenApprovalIds.size > 500) seenApprovalIds.delete(seenApprovalIds.values().next().value);
    return true;
  };

  // ---- commands from SSE (wired via ctx.connectEvents in app.js)
  function handleCommand(cmd) {
    const a = cmd.action;
    try {
      // Focus's own commands go to Focus whatever is playing (focus_start switches to it).
      if (a.startsWith("focus_")) { backends.get("focus")?.command(cmd); return; }
      // Another backend is playing: it takes the transport commands it knows.
      if (a !== "load" && activeBackend !== "webamp" && backends.get(activeBackend)?.command(cmd)) return;
      switch (a) {
        case "play": api.play(); break;
        case "pause": api.pause(); break;
        case "toggle": api.toggle(); break;
        case "stop": api.stop(); break;
        case "next": api.next(); break;
        case "prev": api.prev(); break;
        case "seek": api.seek(cmd.seconds || 0); break;
        case "seek_by": api.seekBy(cmd.seconds); break;
        case "rate": { const r = api.setRate(cmd.rate); api.saveBookRate(currentInfo().item, r); break; }
        case "sleep": api.setSleep(cmd); break;
        case "queue_remove": queueOps.remove(cmd.index | 0); break;
        case "queue_move": queueOps.move(cmd.from | 0, cmd.to | 0); break;
        case "queue_clear": queueOps.clear(); break;
        case "volume": api.setVolume(cmd.percent); break;
        case "shuffle": api.setShuffle(cmd.on); break;
        case "repeat": api.setRepeat(cmd.on); break;
        case "load": api.loadTracks(cmd.items || [], { mode: cmd.mode || "replace", play: cmd.play !== false }); break;
        case "play_index": api.playIndex(cmd.index | 0); break;
        case "eq": api.setEq(cmd); break;
        case "skin": api.setSkin(cmd.id); break;
        case "visualizer": api.toggleVisualizer?.(typeof cmd.open === "boolean" ? cmd.open : undefined); break;
        case "player_location": window.parent.postMessage({ type: "revamp:location", location: cmd.location }, "*"); break;
        case "toast": toast(cmd.text || "", {}); break;
        case "settings": { // from Settings → REVamp in Hermes; only these keys
          const patch = {};
          if (typeof cmd.duck_on_approval === "boolean") patch.duck_on_approval = cmd.duck_on_approval;
          if (typeof cmd.duck_chime === "boolean") patch.duck_chime = cmd.duck_chime;
          if (Number.isFinite(cmd.duck_dip_seconds)) patch.duck_dip_seconds = Math.min(5, Math.max(1, Math.round(cmd.duck_dip_seconds)));
          if (Number.isFinite(cmd.duck_level)) patch.duck_level = Math.min(1, Math.max(0, cmd.duck_level));
          if (typeof cmd.duck_toast === "boolean") patch.duck_toast = cmd.duck_toast;
          if (cmd.duck_approval_scope === "app" || cmd.duck_approval_scope === "all") patch.duck_approval_scope = cmd.duck_approval_scope;
          if (Object.keys(patch).length) api.setSavedSettings(patch);
          break;
        }
        default: break;
      }
    } catch (e) { console.warn("command failed", a, e); }
  }

  // ---- agent events: ticker, activity dot, approval banner, ducking
  function handleAgentEvents(events, bulk = false) {
    for (const ev of events) {
      ctx.pushTicker([ev]);
      if (ev.kind === "tool_start") { ctx.setAgentBusy(true); ctx.setApprovalWaiting(false); }
      if (ev.kind === "tool_end" || ev.kind === "session_end") ctx.setAgentBusy(false);
      // A tool that finished was approved (or never needed approval): nobody is waiting any more.
      if (ev.kind === "tool_end") ctx.setApprovalWaiting(false);
      if (ev.kind === "session_end") { ctx.setApprovalWaiting(false); unduck(null, approvalSession(ev)); } // nobody left to answer
      if (ev.kind === "approval_request") {
        const actionable = approvalIsActionable(ev);
        ctx.setApprovalWaiting(actionable, actionable ? duckReason(ev) : "");
        if (alertFor(ev)) duckTo(duckLevel(), duckReason(ev), ev.id, approvalSession(ev));
      }
      if (ev.kind === "approval_response") { ctx.setApprovalWaiting(false); unduck(null, approvalSession(ev)); }
    }
    if (bulk) { // replayed log: reflect latest states without alert side effects
      const last = events[events.length - 1];
      // Only a recent unanswered request: an old one in the log (its answer never reported) must not
      // light the banner forever after a reload.
      const fresh = last?.ts && Date.now() / 1000 - last.ts < 600;
      if (last?.kind === "approval_request" && fresh && approvalIsActionable(last)) ctx.setApprovalWaiting(true, duckReason(last));
    }
  }

  // ---- queue editing, by position (Hermes mini player). Webamp's tracks carry our entry ids
  // (see `entries`), so an edit is just a Webamp action; items can't come loose from it.
  // Refused while Webamp is mid-swap to a new queue: positions would be the old queue's.
  const queueOps = {
    snapshot() {
      if (!webampSettled()) return null;
      const pl = storeState()?.playlist || {};
      const order = Array.isArray(pl.trackOrder) ? [...pl.trackOrder] : [];
      const list = webampItems();
      return list.length === order.length ? { list, order, cur: pl.currentTrack } : null;
    },
    commit(list, order, curId) {
      savedQueue = list;
      stagePersist({ playlist: [...list], current_index: Math.max(0, order.indexOf(curId)) });
      persistNow(); // the mini player re-reads the saved queue right after the command
      updateNowPlayingSoon();
      setTimeout(beat, 50);
    },
    remove(i) {
      const snap = this.snapshot();
      if (!snap) return;
      const { list, order, cur } = snap;
      if (!(i >= 0 && i < order.length)) return;
      const [id] = order.splice(i, 1);
      list.splice(i, 1);
      const wasPlaying = media().status === "PLAYING";
      dispatchStore({ type: "REMOVE_TRACKS", ids: [id] });
      let curId = cur;
      if (id === cur) { // the playing item: carry on with the one that took its place
        curId = order[Math.min(i, order.length - 1)];
        try {
          if (curId === undefined) webamp.stop();
          else { webamp.setCurrentTrack(curId); if (wasPlaying) webamp.play(); else webamp.stop(); }
        } catch { /* guard */ }
      }
      this.commit(list, order, curId);
    },
    move(from, to) {
      const snap = this.snapshot();
      if (!snap) return;
      const { list, order, cur } = snap;
      if (!(from >= 0 && from < order.length) || !(to >= 0 && to < order.length) || from === to) return;
      order.splice(to, 0, order.splice(from, 1)[0]);
      list.splice(to, 0, list.splice(from, 1)[0]);
      dispatchStore({ type: "SET_TRACK_ORDER", trackOrder: order });
      this.commit(list, order, cur);
    },
    clear() {
      try { webamp.stop(); } catch { /* guard */ }
      dispatchStore({ type: "REMOVE_ALL_TRACKS" });
      this.commit([], [], undefined);
    },
  };
  api.queueOps = queueOps;

  // ---- sleep timer: pause after N minutes or at the end of this track. The volume fades out
  // over the last ~20 s, then the player pauses and the volume goes back to where it was.
  // Runs here so it works whichever surface (full page, Hermes mini player, agent) set it.
  const SLEEP_FADE_S = 20;
  let sleep = null; // { mode: "time", endsAt, total } | { mode: "track", trackId }
  let sleepFrom = null; // the volume before the fade began
  let sleepTimer = null;
  function sleepLeft() {
    if (!sleep) return null;
    if (sleep.mode === "time") return Math.max(0, (sleep.endsAt - Date.now()) / 1000);
    const pl = storeState()?.playlist;
    if (pl?.currentTrack !== sleep.trackId) return 0; // that track ended (or was skipped)
    const m = media();
    if (sleepFrom != null && m.status !== "PLAYING") return 0; // last track ran out mid-fade
    const dur = storeState()?.tracks?.[pl.currentTrack]?.duration || currentInfo().duration;
    return dur > 0 ? Math.max(0, dur - (m.timeElapsed || 0)) : null;
  }
  function sleepTick() {
    const left = sleepLeft();
    if (left == null) return;
    if (left <= 0.05) return finishSleep();
    const fade = sleep.mode === "time" ? Math.min(SLEEP_FADE_S, sleep.total / 2) : SLEEP_FADE_S;
    // Play check must cover every backend: Focus pauses Webamp, so Webamp's own
    // status stays PAUSED while the drone plays and the fade would never run.
    const focus = backends.get("focus");
    const focusPlaying = (() => { try { return focus?.status?.()?.state === "playing"; } catch { return false; } })();
    const playing = media().status === "PLAYING" || (activeBackend === "focus" && focusPlaying);
    if (left <= fade && playing) {
      if (sleepFrom == null) sleepFrom = currentVolume();
      if (activeBackend === "focus") {
        try { focus?.command({ action: "volume", percent: Math.round(sleepFrom * (left / fade)) }); } catch { /* guard */ }
      } else {
        try { webamp.setVolume(Math.round(sleepFrom * (left / fade))); } catch { /* guard */ }
      }
    }
  }
  function stopSleep() {
    clearInterval(sleepTimer);
    sleepTimer = null;
    sleep = null;
    if (sleepFrom != null) try { webamp.setVolume(sleepFrom); } catch { /* guard */ }
    sleepFrom = null;
    paintNpExtras();
    setTimeout(beat, 50);
  }
  function finishSleep() {
    // Pause whatever is playing: Webamp, or another backend such as Focus.
    if (activeBackend !== "webamp") backends.get(activeBackend)?.command({ action: "pause" });
    else api.pause(); // only if playing: Webamp's pause() would restart paused music
    stopSleep(); // after the pause: the restored volume must not be heard
    toast("Sleep timer: paused", { kind: "ok" });
  }
  function sleepStatus() {
    const left = sleepLeft();
    return sleep ? { mode: sleep.mode, remaining: left == null ? null : Math.ceil(left) } : null;
  }
  /** {minutes} (0 = off), {end_of_track: true}, or {seconds} (exact; tests use short ones). */
  api.setSleep = ({ minutes, seconds, end_of_track } = {}) => {
    stopSleep();
    if (end_of_track) {
      const info = currentInfo();
      if (!info.item || info.item.kind === "stream" || info.item.kind === "focus") {
        toast("This source has no track end: pick a time instead", {});
        return;
      }
      sleep = { mode: "track", trackId: storeState()?.playlist?.currentTrack };
    } else {
      const total = Number(seconds) > 0 ? Number(seconds) : Number(minutes) * 60;
      if (!(total > 0)) return;
      sleep = { mode: "time", endsAt: Date.now() + total * 1000, total };
    }
    sleepTimer = setInterval(sleepTick, 250);
    paintNpExtras();
    setTimeout(beat, 50);
  };
  api.sleepStatus = sleepStatus;

  // ---- likes (local tracks): one set shared by every view; the server tells us about
  // likes made elsewhere (the Hermes mini player) over SSE.
  let likedIds = new Set();
  const likesChanged = () => { paintNpExtras(); window.dispatchEvent(new CustomEvent("revamp:likes")); };
  apiGet("/api/likes").then((r) => { likedIds = new Set(r?.ids || []); likesChanged(); }).catch(() => {});
  window.addEventListener("revamp:likes-sse", (e) => {
    if (Array.isArray(e.detail?.ids)) { likedIds = new Set(e.detail.ids); likesChanged(); }
  });
  api.isLiked = (trackId) => likedIds.has(trackId);
  api.setLiked = async (trackId, liked) => {
    if (!trackId) return;
    const before = new Set(likedIds);
    if (liked) likedIds.add(trackId); else likedIds.delete(trackId);
    likesChanged();
    try {
      const r = await apiPost("/api/likes", { track_id: trackId, liked: !!liked });
      likedIds = new Set(r?.ids || []);
    } catch (e) { likedIds = before; toast(`Like failed: ${e.message}`, { kind: "error" }); }
    likesChanged();
  };

  // The heart and sleep control on the now-playing card (full page).
  const npLike = document.getElementById("np-like");
  const npSleep = document.getElementById("np-sleep");
  const npSleepLeft = document.getElementById("np-sleep-left");
  let npItem = null;
  function paintNpExtras(it = npItem) {
    npItem = it;
    if (npLike) {
      const id = it?.kind !== "stream" ? localTid(it) : null;
      npLike.hidden = !id;
      const on = !!id && likedIds.has(id);
      npLike.setAttribute("aria-pressed", String(on));
      npLike.textContent = on ? "♥" : "♡";
      npLike.title = on ? "Unlike" : "Like";
    }
    if (npSleepLeft) {
      const st = sleepStatus();
      npSleepLeft.textContent = !st ? "" : st.mode === "track" ? "sleep: end of track" : `sleep ${fmtLeft(st.remaining)}`;
      if (npSleep && document.activeElement !== npSleep) npSleep.value = !st ? "0" : st.mode === "track" ? "track" : npSleep.value;
    }
  }
  npLike?.addEventListener("click", () => { const id = localTid(npItem); if (id) api.setLiked(id, !likedIds.has(id)); });
  npSleep?.addEventListener("change", () => {
    const v = npSleep.value;
    api.setSleep(v === "track" ? { end_of_track: true } : { minutes: Number(v) });
  });
  setInterval(() => { if (!document.hidden && sleep) paintNpExtras(); }, 1000);

  // ---- OS media keys and lock-screen controls. Webamp's built-in version is off: it would
  // offer seeking on live radio. Hermes' own keyboard shortcuts go through commands, untouched.
  let msKey = "";
  function updateMediaSession(info, title, artist, art) {
    const ms = navigator.mediaSession;
    if (!ms) return;
    const isStream = info.item?.kind === "stream";
    const key = JSON.stringify([title, artist, art, isStream]);
    if (key !== msKey) {
      msKey = key;
      try {
        ms.metadata = new MediaMetadata({ title, artist, album: info.item?.album || "", artwork: art ? [{ src: art }] : [] });
      } catch { /* old browser */ }
      const set = (action, fn) => { try { ms.setActionHandler(action, fn); } catch { /* unsupported action */ } };
      set("play", () => handleCommand({ action: "play" }));
      set("pause", () => handleCommand({ action: "pause" }));
      set("stop", () => handleCommand({ action: "stop" }));
      set("previoustrack", () => handleCommand({ action: "prev" }));
      set("nexttrack", () => handleCommand({ action: "next" }));
      set("seekbackward", isStream ? null : (d) => handleCommand({ action: "seek_by", seconds: -(d?.seekOffset || 10) }));
      set("seekforward", isStream ? null : (d) => handleCommand({ action: "seek_by", seconds: d?.seekOffset || 10 }));
      set("seekto", isStream ? null : (d) => {
        if (Number.isFinite(d?.seekTime)) handleCommand({ action: "seek", seconds: d.seekTime });
      });
    }
    try { ms.playbackState = info.status === "PLAYING" ? "playing" : info.status === "PAUSED" ? "paused" : "none"; } catch { /* guard */ }
    if (!isStream && info.duration > 0) {
      try { ms.setPositionState({ duration: info.duration, position: Math.min(Math.max(0, info.position || 0), info.duration), playbackRate: 1 }); } catch { /* guard */ }
    }
  }

  // expose for views (library picker, playlist save, agent settings)
  window.revampPlayerApi = api;
  api.setSavedSettings = (s) => { saved.settings = { ...saved.settings, ...s }; stagePersist({ settings: saved.settings }); };
  api.mainVisualizerPrefs = () => mainVisPrefs(saved.settings);
  api.getSettings = () => ({ ...saved.settings });
  api.nowStatus = () => safe(() => currentInfo().status) || "STOPPED"; // PLAYING / PAUSED / STOPPED
  api.openSaveQueueModal = openSaveQueue;
  api.handleCommand = handleCommand;
  api.handleAgentEvents = handleAgentEvents;

  // ---- modals used by webamp callbacks
  async function openAddUrl() {
    const { textPrompt } = await import("./views/modal.js");
    const url = await textPrompt("Add stream URL", { label: "Stream URL", placeholder: "https://…" });
    if (!url) return;
    const item = { kind: "stream", title: url.replace(/^https?:\/\//, "").slice(0, 60), artist: "", url, duration: null };
    await api.loadTracks([item], { mode: "append", play: false });
    toast("Stream added to playlist", { kind: "ok" });
  }
  async function openSaveQueue() {
    const { textPrompt, toast: t } = await import("./views/modal.js");
    const name = await textPrompt("Save current queue as playlist", { label: "Playlist name" });
    if (!name) return;
    const items = webampSettled() ? webampItems() : savedQueue;
    try {
      await apiPost(`/api/playlists`, { name, items });
      window.dispatchEvent(new Event("revamp:playlists-changed"));
      t(`Saved playlist “${name}”`, { kind: "ok" });
    } catch (e) { t(`Save failed: ${e.message}`, { kind: "error" }); }
  }
  // file picker for webamp context menu → library view multi-select
  window.revampPickFiles = (opts = {}) =>
    new Promise((resolve) => {
      window.revampSetView("library");
      import("./views/library.js").then((m) => m.requestLibraryPick((tracks) => {
        // toWebampTrack files each item under its entry id, so Webamp's copy maps back.
        resolve(normalizeItems(tracks).filter(isWebampItem).map(toWebampTrack));
      }));
    });

  // initial paint + start SSE subscription owned by app via ctx
  updateNowPlaying();
  setTimeout(checkRadio, 500);
  // The visualizer comes back where it was once Webamp has rendered (its window must
  // exist). The bundle boots with MilkDrop open: close it unless it was remembered open.
  // Without WebGL we always end closed, so a later click explains itself with a toast.
  Promise.resolve().then(() => {
    const wantOpen = hasWebGL() && wantsMilkdropOpen();
    placeMilkdrop();
    if (api.visualizerOpen() !== wantOpen) api.toggleVisualizer(wantOpen);
    // Webamp centres the classic windows after the first paint. Pin them again
    // once that pass has run, or the desktop window is sized around the offset.
    if (visualizerPlacement === "desktop") {
      api.layoutDesktop();
      requestAnimationFrame(() => api.layoutDesktop());
      setTimeout(() => api.layoutDesktop(), 250);
    }
  });
  ctx.connectEvents(
    handleCommand,
    (evs, bulk) => handleAgentEvents(evs, bulk),
    () => window.revampRefreshView?.(), // library changed
    (state) => { if (state?.settings) { saved.settings = state.settings; applyMainVis(); } }, // state broadcast: adopt settings
  );

  return api;
}

// Cycle Webamp's analyzer mode to the saved one. Only TOGGLE_VISUALIZER_STYLE
// exists; the index is 0 spectrum, 1 oscilloscope, 2 off.
function alignMainVisMode(webamp, settings) {
  const want = MAIN_VIS_MODES.indexOf(mainVisPrefs(settings).mode);
  if (want < 0) return;
  for (let guard = 0; guard < MAIN_VIS_MODES.length; guard++) {
    let style = 0;
    try { style = webamp.store.getState().display.visualizerStyle ?? 0; } catch { return; }
    if (style === want) return;
    try { webamp.store.dispatch({ type: "TOGGLE_VISUALIZER_STYLE" }); } catch { return; }
  }
}

// The bar/scope instance lives in the React hook list above the canvas.
function visInstance(canvas) {
  const key = Object.keys(canvas).find((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$"));
  if (!key) return null;
  let fiber = canvas[key];
  for (let depth = 0; fiber && depth < 15; depth += 1, fiber = fiber.return) {
    let hook = fiber.memoizedState;
    for (let hops = 0; hook && hops < 40; hops += 1, hook = hook.next) {
      const state = hook.memoizedState;
      const candidates = [state, Array.isArray(state) ? state[0] : null, state?.current];
      for (const candidate of candidates) {
        if (candidate && typeof candidate.paintFrame === "function" && candidate._vis) return candidate;
      }
    }
  }
  return null;
}

// ---------------------------------------------------------------- utils
function fmtLeft(s) { // "23m", "45s", "1h 05m"
  if (!Number.isFinite(s)) return "";
  if (s < 60) return `${Math.max(0, Math.round(s))}s`;
  const m = Math.ceil(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
}
function safe(fn, dflt = undefined) { try { return fn(); } catch { return dflt; } }
function clamp(v, lo = 0, hi = 100) { v = Number(v); if (!Number.isFinite(v)) v = lo; return Math.min(hi, Math.max(lo, Math.round(v))); }
function isWebampItem(x) { return !!x && x.playback?.backend === "webamp" && typeof x.playback.url === "string"; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
