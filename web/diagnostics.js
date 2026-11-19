import { untrustedEmbed } from "./embed-trust.js";
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
  const body = globalThis.document?.body;
  if (body?.classList.contains("embed")) return "frame";
  if (body?.classList.contains("mode-winamp")) return "pop-out";
  return "page";
}

// An error inside an animation loop fires every frame: pass each distinct message on
// at most once, and nothing at all faster than once a second.
const seen = new Set();
let lastSent = 0;

// The server keeps its own private log (revamp.log). A failure in a plain browser tab
// or the pop-out never reaches Hermes' diagnostic storage, so the page also POSTs it to
// /api/client_log. The server bounds and re-cleans this; the limits here keep the request
// small and strip URL queries (which carry the bridge token) before anything is sent.
const SERVER_MAX_MESSAGE = 500;
const SERVER_MAX_STACK = 2000;

function stripQueries(value, limit) {
  let text = String(value ?? "");
  text = text.replace(/https?:\/\/[^\s"'<>]+/gi, (u) => u.split(/[?#]/)[0]);
  text = text.replace(/([?&](?:t|access_token|token|key)=)[^&#\s"']+/gi, "$1[redacted]");
  text = text.replace(/[\r\n\t]+/g, " ");
  return text.slice(0, limit);
}

export function clientLogRecord(kind, detail = {}) {
  const fallback = kind === "rejection" ? "Unhandled promise rejection" : "Unhandled browser error";
  return {
    message: stripQueries(detail.message || fallback, SERVER_MAX_MESSAGE),
    stack: stripQueries(detail.stack, SERVER_MAX_STACK),
    placement: placement(),
  };
}

function sendClientLog(record) {
  try {
    if (typeof fetch !== "function") return;
    const token = globalThis.document?.querySelector?.('meta[name="revamp-token"]')?.content || "";
    if (!token) return; // no key (e.g. a unit harness): nothing to authenticate with
    fetch("/api/client_log", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Revamp-Token": token },
      body: JSON.stringify(record),
      keepalive: true,
    }).catch(() => { /* the log is best-effort; never surface a second error */ });
  } catch { /* a broken report must never break the page */ }
}

function report(kind, detail) {
  const key = `${kind}:${detail?.message}`;
  const now = Date.now();
  if (seen.has(key) || now - lastSent < 1000 || seen.size >= 20) return;
  seen.add(key); lastSent = now;
  const record = diagnosticRecord(kind, { ...detail, placement: placement() });
  if (window.parent !== window && !untrustedEmbed()) window.parent.postMessage({ type: "revamp:diagnostic", record }, "*");
  sendClientLog(clientLogRecord(kind, detail));
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
