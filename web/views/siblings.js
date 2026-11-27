// siblings.js — quiet pointers to sibling plugins worth knowing about
// (Aura, visuals and films that react to your music; Music Studio, which makes songs).
//
// House rules, learned from the catalog review of the same pattern elsewhere:
//   - The ONLY way out of REVamp is the host route: this module never touches
//     the page around it, host search, or anything outside this view — a
//     button here posts one message to the desktop half, which calls
//     host.navigate('/capabilities?tab=plugins&plugin=<id>').
//   - At most one card per place, dismissal remembered in REVamp's saved
//     settings (so it survives restarts and follows the library state).
//   - Nothing here is an ad: one line of copy, real actions, no popups.

export const AURA_ID = "aura";
export const STUDIO_ID = "hermes-music-studio";

/** The public host route to a plugin's entry. Built in exactly one place. */
export const pluginRoute = (id) => `/capabilities?tab=plugins&plugin=${encodeURIComponent(id)}`;

/** Copyable fallback for hosts or surfaces where the button cannot work. */
export const fallbackHint = (name) => `Search for ${name} in Capabilities → Plugins`;

export const AURA_LINE = "Want visuals that move with your music? Aura plays films and audio-reactive scenes in its own pane.";
export const STUDIO_LINE = "Make your own songs with Music Studio";

// -- dismissal (persisted in REVamp's saved settings) -----------------------

export const DISMISS_KEY = "siblingHintsDismissed";

/** Is the hint for `key` dismissed in these saved settings? */
export const isDismissed = (settings, key) =>
  Boolean(settings && settings[DISMISS_KEY] && settings[DISMISS_KEY][key]);

/** The next saved-settings map after dismissing `key` (pure; never mutates). */
export const dismissalAfter = (settings, key, now = Date.now()) => ({
  ...(settings?.[DISMISS_KEY] || {}),
  [key]: Math.round(now / 1000),
});

/** Persist the dismissal through the player's saved settings. Best effort. */
export function dismiss(pApi, key) {
  try {
    const cur = pApi?.getSettings?.() || {};
    pApi?.setSavedSettings?.({ [DISMISS_KEY]: dismissalAfter(cur, key) });
    return dismissalAfter(cur, key);
  } catch {
    return null;
  }
}

// -- the one door out: ask the desktop half to navigate ---------------------

/** True when this page lives inside Hermes (the desktop half is listening). */
export const canAskHost = () =>
  typeof window !== "undefined" && window.parent && window.parent !== window;

/** Ask the desktop half to open the host's Plugins page at `id`. */
export function askHostForPlugin(id) {
  if (!canAskHost()) return false;
  try {
    window.parent.postMessage({ type: "revamp:open-plugin", plugin: id }, "*");
    return true;
  } catch {
    return false;
  }
}

// -- the card ---------------------------------------------------------------
// A row inside REVamp's own tree: the line, the actions, a quiet dismiss.

export function buildCard({ key, name, line, extra = null, onDismiss = null }) {
  const d = document.createElement("div");
  d.className = "sib-card";
  d.setAttribute("data-sib", key);

  const text = document.createElement("span");
  text.className = "sib-text";
  text.textContent = line;
  d.appendChild(text);

  const acts = document.createElement("span");
  acts.className = "sib-actions";
  if (canAskHost()) {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "btn sm btn-ghost sib-open";
    open.textContent = `Find ${name} in Plugins`;
    open.setAttribute("aria-label", `Find ${name} in Plugins`);
    open.addEventListener("click", () => askHostForPlugin(key === "aura" ? AURA_ID : STUDIO_ID));
    acts.appendChild(open);
  }
  // The host route can be unreachable (plain browser, pop-out): the fallback
  // stays readable and copyable either way.
  const fb = document.createElement("span");
  fb.className = "sib-fallback";
  fb.title = "Copyable: how to find it by hand";
  fb.textContent = fallbackHint(name);
  acts.appendChild(fb);
  if (extra) acts.appendChild(extra);
  d.appendChild(acts);

  if (onDismiss) {
    const x = document.createElement("button");
    x.type = "button";
    x.className = "btn sm btn-ghost sib-dismiss";
    x.textContent = "Not now";
    x.setAttribute("aria-label", "Dismiss this suggestion");
    x.addEventListener("click", () => onDismiss());
    d.appendChild(x);
  }
  return d;
}

/**
 * Mount the card for `key` into `holder` unless it is dismissed (or the holder
 * already shows it — views re-render on every poll). `force` (a click on a menu
 * item) overrides a past dismissal; Not-now still works. Returns the card, or
 * null when nothing was mounted.
 */
export function mountHint({ holder, key, name, line, pApi, extra = null, force = false }) {
  const settings = (() => {
    try { return pApi?.getSettings?.() || {}; } catch { return {}; }
  })();
  if (!force && isDismissed(settings, key)) return null;
  if (holder.__sib === key) return null; // already on screen; never stack copies
  const card = buildCard({
    key, name, line, extra,
    onDismiss: () => {
      dismiss(pApi, key);
      holder.__sib = null;
      card.remove();
    },
  });
  holder.__sib = key;
  holder.appendChild(card);
  return card;
}

// -- Music Studio's songs folder --------------------------------------------
// Music Studio writes finished takes (.wav) into its data dir, or wherever its
// library.json pointer aims. When that folder exists, offer to add it with the
// ordinary library-folders setting; when it does not, offer nothing.

export async function loadStudioSongsDir(api) {
  try {
    const res = await api("/api/siblings");
    const dir = res && typeof res.studio_songs_dir === "string" ? res.studio_songs_dir : "";
    return dir || null;
  } catch {
    return null;
  }
}

/** The PUT body adding `dir` to the current folders (pure; dedupes). */
export const foldersWith = (folders, dir) => {
  const out = (folders || [])
    .filter((f) => f && typeof f.path === "string")
    .map((f) => ({ path: f.path, kind: f.kind === "books" ? "books" : "music" }));
  if (!out.some((f) => f.path === dir)) out.push({ path: dir, kind: "music" });
  return out;
};

/**
 * The "add the songs folder" button, or null when there is nothing to add
 * (no songs dir, an unreachable server, or it is already in the library).
 * `onAdded` runs after a successful save (the caller refreshes the view).
 */
export async function studioFolderButton(api, onAdded) {
  const dir = await loadStudioSongsDir(api);
  if (!dir) return null;
  let have;
  try {
    const res = await api("/api/library/folders");
    if (!res || !Array.isArray(res.folders)) return null;
    have = res.folders;
  } catch {
    // The PUT replaces the whole folder list: without the current list a click
    // would drop every folder the owner already has. Offer nothing instead.
    return null;
  }
  if (have.some((f) => f && f.path === dir)) return null;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn sm sib-folder";
  b.textContent = "Add Music Studio's songs folder to your library";
  b.setAttribute("aria-label", "Add Music Studio's songs folder to your library");
  b.addEventListener("click", async () => {
    // The PUT replaces the whole folder list, and the list may have changed since
    // this button was drawn (a folder added or removed, the library emptied):
    // read it again now, and only then write. A failed read writes nothing.
    let now;
    try {
      const res = await api("/api/library/folders");
      if (!res || !Array.isArray(res.folders)) return;
      now = res.folders;
    } catch {
      return;
    }
    try {
      await api("/api/library/folders", "PUT", { folders: foldersWith(now, dir) });
      b.disabled = true;
      onAdded?.();
    } catch { /* the button stays; the library view shows server errors */ }
  });
  return b;
}
