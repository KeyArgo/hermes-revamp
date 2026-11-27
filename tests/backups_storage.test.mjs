// Inside Hermes the page is a sandboxed iframe (opaque origin), so touching
// sessionStorage throws. The "Welcome back" restore offer must still appear once
// per page load instead of aborting at the storage read.
import assert from "node:assert/strict";
import test from "node:test";

// backups.js imports modal.js, which registers a document listener at load time.
globalThis.document = { addEventListener() {} };

const { markRestoreOffered, restoreOffered } = await import("../web/views/backups.js");

function useStorage(obj) {
  Object.defineProperty(globalThis, "sessionStorage", { value: obj, configurable: true, writable: true });
}

test("a sessionStorage that throws falls back to an in-memory flag", () => {
  const denied = {
    getItem() { throw new Error("SecurityError: sessionStorage is not available"); },
    setItem() { throw new Error("SecurityError: sessionStorage is not available"); },
  };
  useStorage(denied);
  assert.equal(restoreOffered(), false, "nothing offered yet");
  markRestoreOffered(); // must not throw
  assert.equal(restoreOffered(), true, "the offer is remembered in memory");
});

test("working storage is still used when it is available", () => {
  const store = new Map();
  useStorage({
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, String(v)); },
  });
  // The in-memory flag from the previous case is only consulted when storage throws.
  assert.equal(restoreOffered(), false);
  markRestoreOffered();
  assert.equal(store.get("revamp.restoreOffered"), "1");
  assert.equal(restoreOffered(), true);
});
