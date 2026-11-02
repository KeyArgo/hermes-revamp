// Who may embed this page. It carries the API token (meta tag) and talks to its parent
// with postMessage, so a web site must not be able to frame it and listen in: only
// Hermes (an app origin, file:, or an opaque sandboxed frame) and this computer
// (loopback) may. Chromium (Hermes, the pop-out) reports every ancestor; elsewhere the
// referrer is the best we have.
const LOCAL = (o) => o === "null" || o === "file://"
  || /^(file|app|hermes|electron):/.test(o)
  || /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/.test(o);

export function untrustedEmbed() {
  const w = globalThis.window;
  if (!w || w.parent === w) return false;
  const anc = globalThis.location?.ancestorOrigins;
  if (anc && anc.length) return [...anc].some((o) => !LOCAL(o));
  try {
    const ref = globalThis.document?.referrer;
    if (ref) return !LOCAL(new URL(ref).origin);
  } catch { /* unreadable referrer */ }
  // Framed, and the parent can't be told (a site can hide its referrer): fail closed.
  // Hermes and the pop-out are Chromium, which always reports ancestorOrigins.
  return true;
}
