// Inside Hermes the page is a sandboxed iframe with no allow-popups, so window.open
// and target="_blank" do nothing. Ask the plugin host to open the URL in the user's
// browser instead: desktop/plugin.js accepts revamp:open-external only from REVamp's
// own frames and only for http(s) URLs. Outside Hermes the page is a normal tab, so
// window.open still works there.

export function isEmbedded() {
  return typeof window !== "undefined" && window.parent !== window;
}

/** Open *url* in the browser. Returns false when the URL is not http(s). */
export function openExternal(url) {
  if (typeof url !== "string" || !/^https?:\/\//i.test(url)) return false;
  if (isEmbedded()) {
    try {
      window.parent.postMessage({ type: "revamp:open-external", url }, "*");
      return true;
    } catch {
      return false;
    }
  }
  try {
    window.open(url, "_blank", "noopener,noreferrer");
    return true;
  } catch {
    return false;
  }
}
