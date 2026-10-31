// A tiny, deliberately privacy-safe trail for a player page that failed before
// its normal bug-report UI could render. Hermes keeps the last few records.

const MAX_MESSAGE = 240;
const MAX_STACK = 700;

function clean(value, limit) {
  let text = String(value ?? "").replace(/[\r\n\t]+/g, " ");
  // Queries often hold the bridge token; file paths and URLs can identify a
  // person's account, library, or network. A failure category is useful; those
  // details are not.
  text = text.replace(/https?:\/\/[^\s)]+/gi, "[url]");
  text = text.replace(/[A-Za-z]:\\[^\s:)]+/g, "[path]");
  text = text.replace(/(^|[\s("'=])\/(?!web\/)[^\s/:)]+\/[^\s:)]*/g, "$1[path]");
  text = text.replace(/([?&](?:access_)?token=)[^&\s]+/gi, "$1[redacted]");
  return text.slice(0, limit);
}

export function diagnosticRecord(kind, detail = {}) {
  return {
    ts: Date.now(),
    kind: kind === "rejection" ? "rejection" : "error",
    message: clean(detail.message, MAX_MESSAGE),
    stack: clean(detail.stack, MAX_STACK),
    placement: clean(detail.placement, 32),
  };
}

function placement() {
  const body = document.body;
  if (body?.classList.contains("embed")) return "frame";
  if (body?.classList.contains("mode-winamp")) return "pop-out";
  return "page";
}

// An error inside an animation loop fires every frame: pass each distinct message on
// at most once, and nothing at all faster than once a second.
const seen = new Set();
let lastSent = 0;

function report(kind, detail) {
  const key = `${kind}:${detail?.message}`;
  const now = Date.now();
  if (seen.has(key) || now - lastSent < 1000 || seen.size >= 20) return;
  seen.add(key); lastSent = now;
  const record = diagnosticRecord(kind, { ...detail, placement: placement() });
  if (window.parent !== window) window.parent.postMessage({ type: "revamp:diagnostic", record }, "*");
}

if (typeof window !== "undefined") {
  window.addEventListener("error", (event) => report("error", {
    message: event.message || event.error?.message || "Unhandled browser error",
    stack: event.error?.stack || "",
  }));
  window.addEventListener("unhandledrejection", (event) => report("rejection", {
    message: event.reason?.message || String(event.reason || "Unhandled promise rejection"),
    stack: event.reason?.stack || "",
  }));
}
