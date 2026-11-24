// Hunt 3: a failed library load painted the error and then drew the list over it, so the
// person read "No music yet" (and was sent to Folders) or saw the previous search's songs.
import assert from "node:assert/strict";
import test from "node:test";
import { El, flushTimers, install, settle } from "./library_fake_dom.mjs";

const track = (i) => ({ id: `t${i}`, title: `Song ${i}`, artist: "A", album: "B", duration: 60, track_no: i });

async function renderLibrary(fail) {
  const document = install();
  const api = async (url) => {
    const u = String(url);
    if (u.startsWith("/api/library/status")) return { count: 3, last_scan: 1, scanning: false };
    if (u.startsWith("/api/library/folders")) return { folders: [{ path: "/music", kind: "music" }] };
    if (u.startsWith("/api/library/tracks")) {
      if (fail.tracks && u.includes("q=")) throw new Error("server down");
      return { tracks: [track(0), track(1), track(2)], total: 3 };
    }
    if (u.startsWith("/api/library/artists")) { if (fail.artists) throw new Error("server down"); return []; }
    if (u.startsWith("/api/library/albums")) { if (fail.albums) throw new Error("server down"); return []; }
    if (u === "/api/playlists") return [];
    return {};
  };
  api.authedUrl = (p) => p;
  const { render } = await import("../web/views/library.js");
  const root = new El("div");
  document.body.appendChild(root);
  await render(root, api, "music");
  await settle();
  return document;
}

const listText = (document) => {
  const out = [];
  const walk = (n) => { if (n.textContent) out.push(n.textContent); for (const c of n.children || []) walk(c); };
  walk(document.querySelector("#lib-list"));
  return out.join(" ");
};

test("a failed new search shows the error, not the old songs", async () => {
  const document = await renderLibrary({ tracks: true });
  assert.equal(document.querySelectorAll("#lib-list tr.trow").length, 3);
  const q = document.querySelector("#lib-q");
  q.value = "jazz";
  q.fire("input");
  flushTimers();
  await settle();
  assert.match(listText(document), /server down/);
  assert.equal(document.querySelectorAll("#lib-list tr.trow").length, 0, "no stale rows under the new query");
});

for (const tab of ["artists", "albums"]) {
  test(`a failed ${tab} load shows the error, not "No ${tab} yet"`, async () => {
    const document = await renderLibrary({ [tab]: true });
    document.querySelectorAll(".tabs button").find((b) => b.dataset.t === tab).fire("click");
    await settle();
    const text = listText(document);
    assert.match(text, /server down/);
    assert.doesNotMatch(text, /No (artists|albums) yet/);
  });
}
