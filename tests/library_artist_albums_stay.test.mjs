// An opened artist's albums stay that artist's: the 3 s scan poll reloaded the Albums
// tab without the artist and showed every album, while the search box still read the
// artist's name. Leaving for another tab must not leave that name behind either.
import assert from "node:assert/strict";
import test from "node:test";
import { El, install, intervals, settle } from "./library_fake_dom.mjs";

async function openArtist() {
  const log = [];
  const state = { count: 3 };
  const document = install();
  const api = async (url) => {
    const u = String(url);
    log.push(u);
    if (u.startsWith("/api/library/status")) return { count: state.count, last_scan: 1, scanning: true, progress: {} };
    if (u.startsWith("/api/library/folders")) return { folders: [{ path: "/music", kind: "music" }] };
    if (u.startsWith("/api/library/tracks")) return { tracks: [], total: 0 };
    if (u.startsWith("/api/library/artists")) return [{ name: "Moby", tracks: 2, albums: ["Play"] }, { name: "Zed", tracks: 1, albums: ["Z"] }];
    if (u.startsWith("/api/library/albums")) return [{ album: "Play", artist: "Moby", tracks: 2 }];
    if (u === "/api/playlists") return [];
    return {};
  };
  api.authedUrl = (p) => p;
  globalThis.revampPlayerApi = { isLiked: () => false, loadTracks: async () => {} };
  const { render } = await import("../web/views/library.js");
  const root = new El("div");
  document.body.appendChild(root);
  await render(root, api, "music");
  await settle();
  const poll = intervals.at(-1);
  await poll(); await settle(); // the first poll only records the signature
  document.querySelectorAll(".tabs button").find((b) => b.dataset.t === "artists").fire("click");
  await settle();
  document.querySelectorAll("#lib-list tr.trow")[0].fire("click");
  await settle();
  return { document, log, state, poll };
}

test("the scan poll reloads an opened artist's albums, not every album", async () => {
  const { log, state, poll } = await openArtist();
  const albumCalls = () => log.filter((u) => u.startsWith("/api/library/albums"));
  assert.match(albumCalls().at(-1), /artist=Moby/, "opening the artist asked for its albums");
  const before = albumCalls().length;
  state.count = 4; // a scan found a song
  await poll(); await settle();
  assert.equal(albumCalls().length, before + 1, "the poll reloaded the albums");
  assert.match(albumCalls().at(-1), /artist=Moby/);
});

test("leaving an opened artist for another tab clears the artist's name from the search box", async () => {
  const { document } = await openArtist();
  const q = document.querySelector("#lib-q");
  assert.equal(q.value, "Moby");
  document.querySelectorAll(".tabs button").find((b) => b.dataset.t === "tracks").fire("click");
  await settle();
  assert.equal(q.value, "", "the box matches the list (the whole library)");
});
