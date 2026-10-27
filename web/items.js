// items.js — the canonical queue item, browser side. Mirrors revamp_core/items.py
// (keep the two in step): normalizeItem() upgrades the old {kind: "track"|"stream",
// url, track_id, book} shape and cleans canonical ones.
//   { id, source, kind, title, artist, album, duration, art,
//     playback: { backend: "webamp"|"focus"|"spotify", url?, stream? }, resumeKey?, capabilities: [] }

export const LEGACY_BOOK_SECS = 20 * 60;
const LOCAL_ID = /^[0-9a-f]{16}$/;
const TEXT = ["title", "artist", "album", "art"];

export function hashId(s) { // FNV-1a 32 over UTF-8, same as items.py _hash
  let h = 0x811c9dc5;
  for (const b of new TextEncoder().encode(String(s))) h = Math.imul(h ^ b, 0x01000193) >>> 0;
  return h.toString(16).padStart(8, "0");
}
export const localId = (tid) => `local:track:${tid}`;
export function localTrackId(id) {
  let s = String(id || "");
  if (s.startsWith("local:track:")) s = s.slice(12);
  return LOCAL_ID.test(s) ? s : null;
}
export const radioStreamUrl = (url) => "/api/radio/stream?url=" + encodeURIComponent(url);
export function defaultCapabilities(kind) {
  if (kind === "audiobook") return ["seek", "resume"];
  if (kind === "music") return ["seek"];
  return [];
}
const num = (v) => { const f = Number(v); return v !== null && v !== "" && Number.isFinite(f) && f >= 0 ? f : null; };

function fromLegacy(raw) {
  const url = raw.url;
  if (typeof url !== "string" || !url) return null;
  const duration = num(raw.duration);
  if (raw.kind === "stream") {
    return {
      id: raw.station_id ? `radio:station:${raw.station_id}` : `radio:url:${hashId(url)}`,
      source: "radio", kind: "stream", title: raw.title, artist: raw.artist, duration: null,
      playback: { backend: "webamp", url: radioStreamUrl(url), stream: url },
    };
  }
  const tid = raw.track_id || (url.startsWith("/api/media/") ? url.split("/").pop() : null);
  const kind = raw.book == null ? ((duration || 0) > LEGACY_BOOK_SECS ? "audiobook" : "music") : (raw.book ? "audiobook" : "music");
  if (tid && localTrackId(tid)) {
    return {
      id: localId(tid), source: "local", kind, title: raw.title, artist: raw.artist, album: raw.album,
      duration, art: `/api/art/${tid}`, playback: { backend: "webamp", url },
    };
  }
  return { id: `url:${hashId(url)}`, source: "url", kind, title: raw.title, artist: raw.artist, album: raw.album, duration, playback: { backend: "webamp", url } };
}

export const isCanonical = (x) => !!x && typeof x === "object" && typeof x.id === "string" && !!x.id && !!x.playback && typeof x.playback === "object";

/** Any queue item -> a clean canonical item, or null. */
export function normalizeItem(raw) {
  if (!raw || typeof raw !== "object") return null;
  const item = isCanonical(raw) ? { ...raw } : fromLegacy(raw);
  if (!item) return null;
  const pb = { ...(item.playback || {}) };
  pb.backend = typeof pb.backend === "string" && pb.backend ? pb.backend : "webamp";
  if (pb.backend === "webamp" && !(typeof pb.url === "string" && pb.url)) return null;
  item.playback = pb;
  item.id = String(item.id);
  if (!(typeof item.source === "string" && item.source)) item.source = item.id.includes(":") ? item.id.split(":")[0] : "url";
  if (!(typeof item.kind === "string" && item.kind) || item.kind === "track") item.kind = "music";
  for (const k of TEXT) item[k] = typeof item[k] === "string" && item[k] ? item[k] : null;
  item.duration = num(item.duration);
  item.capabilities = Array.isArray(item.capabilities) ? item.capabilities.filter((c) => typeof c === "string") : defaultCapabilities(item.kind);
  if (item.capabilities.includes("resume")) item.resumeKey = typeof item.resumeKey === "string" && item.resumeKey ? item.resumeKey : item.id;
  else delete item.resumeKey;
  for (const k of ["url", "track_id", "station_id", "book", "resume"]) delete item[k];
  return item;
}
export const normalizeItems = (list) => (Array.isArray(list) ? list.map(normalizeItem).filter(Boolean) : []);

/** The old track|stream kind, still sent in heartbeats for older UIs. */
export const legacyKind = (it) => (it?.kind === "stream" || it?.kind === "focus" ? "stream" : "track");
/** What old UIs compare URLs against: a station's own URL, else the playback URL. */
export const legacyUrl = (it) => it?.playback?.stream || it?.playback?.url || null;
