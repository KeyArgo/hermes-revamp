// adapter.js — the one seam between Revamp's UI and the Focus engine, now
// running behind the "focus" backend.
//
// The Focus view (and, through SSE commands later, the mini player) only call
// the controller returned here: status/on/start/stop/toggle/set/setTimer.
// Today it drives a page-local FocusEngine and handles the "one thing plays at
// a time" rule against Webamp itself. The shared playback controller invokes
// `window.revampFocusActivate` when available and leaves the views alone.
import { FocusEngine, FOCUS_DEFAULTS, FOCUS_TIMERS, cleanParams } from "./engine.js";

export { FOCUS_DEFAULTS, FOCUS_TIMERS };

let controller = null;

/** `api` is the views' convention: api(path, method = "GET", body). */
export function getFocus(api) {
  if (controller) return controller;
  const engine = new FocusEngine();
  let saveTimer = null;
  const save = () => { // settings live in state.json under "focus"
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const { volume, ...params } = engine.params; // volume follows the player; not a Focus setting
      api?.("/api/state", "PUT", { focus: params })?.catch?.(() => {});
    }, 800);
  };
  // Saved settings arrive a moment after boot; until then the defaults apply.
  api?.("/api/state")?.then?.((st) => {
    if (st?.focus && typeof st.focus === "object") {
      engine.set(cleanParams({ ...st.focus, volume: engine.params.volume }));
    }
  }).catch?.(() => {});

  // One thing plays at a time: music starting stops Focus; Focus starting pauses music.
  let unbind = null;
  const bindPlayer = () => {
    const store = window.revampPlayerApi?.webamp?.store;
    if (unbind || !store) return;
    let last = null;
    const onStore = () => {
      const m = store.getState()?.media || {};
      if (typeof m.volume === "number") {
        engine.set({ volume: m.volume / 100 }, { seconds: 0.2 });
      }
      if (m.status === "PLAYING" && last !== "PLAYING" && engine.state !== "stopped") {
        engine.stop({ fade: 1 });
      }
      last = m.status;
    };
    onStore();
    unbind = store.subscribe(onStore);
  };

  controller = {
    engine,
    status: () => engine.status(),
    on: (fn) => engine.on(fn),
    async start() {
      bindPlayer();
      if (window.revampFocusActivate) return window.revampFocusActivate();
      const pa = window.revampPlayerApi;
      try {
        if (pa?.webamp?.store?.getState()?.media?.status === "PLAYING") pa.pause();
      } catch {
        /* no player */
      }
      return engine.start();
    },
    stop: (opts) => engine.stop(opts ?? { fade: 2 }), // a click: quicker than the timer's 6 s fade
    pause: () => engine.pause(),
    resume: () => engine.resume(),
    toggle() {
      return engine.state === "stopped" ? controller.start() : engine.toggle();
    },
    set(patch) {
      const s = engine.set(patch);
      save();
      return s;
    },
    setTimer(minutes) {
      const s = engine.setTimer(minutes);
      save();
      return s;
    },
  };
  window.revampFocus = controller; // for tests and the future command routing
  return controller;
}
