// Hunt 3: the 3 s scan poll reloads the song list while a scan runs (or Music Studio
// adds a song). That reload cleared the selection, so "Like" in an open song menu, or
// "▶ Play" on the toolbar, then acted on every song on screen instead of the chosen ones.
import assert from "node:assert/strict";
import test from "node:test";
import { El, install, intervals, settle } from "./library_fake_dom.mjs";

const track = (i) => ({ id: `t${i}`, title: `Song ${i}`, artist: "A", album: "B", duration: 60, track_no: i });

function makeApi(log, state) {
  const api = async (url, method = "GET", body) => {
    const u = String(url);
    log.push({ url: u, method, body });
    if (u.startsWith("/api/library/status")) return { count: state.count, last_scan: 1, scanning: true, progress: {} };
    if (u.startsWith("/api/library/folders")) return { folders: [{ path: "/music", kind: "music" }] };
    if (u.startsWith("/api/library/tracks")) {
      const n = Math.min(state.count, 200);
      return { tracks: Array.from({ length: n }, (_, k) => track(k)), total: state.count };
    }
    if (u === "/api/playlists") return [];
    return {};
  };
  api.authedUrl = (p) => p;
  return api;
}

async function renderWithPoll() {
  const log = [];
  const state = { count: 5 };
  const document = install();
  const likes = [];
  const played = [];
  globalThis.revampPlayerApi = {
    isLiked: () => false,
    setLiked: (id, liked) => likes.push([id, liked]),
    playItemsAt: async (i, list) => played.push(list.map((x) => x.id ?? x.track_id ?? x.title)),
    loadTracks: async () => {},
  };
  const { render } = await import("../web/views/library.js");
  const root = new El("div");
  document.body.appendChild(root);
  await render(root, makeApi(log, state), "music");
  await settle();
  const poll = intervals.at(-1);
  assert.ok(poll, "the scan poll is set up");
  await poll(); await settle(); // first poll only records the signature
  return { document, root, state, likes, played, poll, log };
}

test("Like in a song's menu still likes only that song after the scan poll reloads the list", async () => {
  const { document, state, likes, poll, log } = await renderWithPoll();
  const row = document.querySelectorAll("#lib-list tr.trow")[2];
  row.querySelector(".album-more").fire("click");
  await settle();
  const like = document.querySelectorAll('.track-menu [role="menuitem"]').find((b) => b.textContent === "Like");
  assert.ok(like, "the menu has Like");

  state.count = 6; // the scan found a new song
  const before = log.filter((c) => c.url.startsWith("/api/library/tracks")).length;
  await poll(); await settle();
  assert.equal(log.filter((c) => c.url.startsWith("/api/library/tracks")).length, before + 1, "the poll reloaded the list");

  like.fire("click");
  assert.deepEqual(likes, [["t2", true]]);
});

test("a selection survives the scan poll's reload (the toolbar acts on the chosen rows)", async () => {
  const { document, state, poll } = await renderWithPoll();
  const rows = document.querySelectorAll("#lib-list tr.trow");
  rows[1].fire("click");
  await settle();
  state.count = 7;
  await poll(); await settle();
  const selected = document.querySelectorAll("#lib-list tr.trow").filter((r) => r.classList.contains("selected"));
  assert.deepEqual(selected.map((r) => String(r.dataset.i)), ["1"]);
});
