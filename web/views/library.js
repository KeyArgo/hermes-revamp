// library.js — the Music and Books views: search, Tracks/Artists/Albums, table, play/enqueue,
// folders, rescan. Exposes requestLibraryPick(cb) for the webamp file-picker integration.
import { mountFolders } from "./folders.js";
import { openModal, textPrompt, toast } from "./modal.js";

let pickMode = false;
let pickCb = null;

/** Called by player.js on behalf of webamp's filePicker: resolve with chosen tracks. */
export function requestLibraryPick(cb) {
  pickMode = true;
  pickCb = cb;
}

const trackToItem = (t) => ({
  kind: "track", title: t.title, artist: t.artist || "", album: t.album || "",
  url: t.url, duration: t.duration ?? null, track_id: t.id,
  ...(t.kind ? { book: t.kind === "audiobook" } : {}),
});

export const renderMusic = (root, api) => render(root, api, "music");
export const renderBooks = (root, api) => render(root, api, "books");

const fmtClock = (s) => {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
  return `${h ? `${h}:${String(m).padStart(2, "0")}` : m}:${String(s % 60).padStart(2, "0")}`;
};

export async function render(root, api, kind = "music") {
  const books = kind === "books";
  const apiKind = books ? "audiobook" : "music"; // what the server calls a track's kind
  let tab = "tracks";
  let query = "";
  let tracks = [], total = 0, offset = 0;
  let tracksGeneration = 0;
  let artists = [], albums = [];
  let selected = new Set();
  let currentAlbum = null;
  let loadMoreObserver = null;

  root.innerHTML = "";

  // ---- toolbar
  const bar = document.createElement("div");
  bar.className = "btn-row lib-search-toolbar";
  bar.style.marginBottom = "10px";
  bar.innerHTML = `
    <div class="searchbox" style="flex:1;min-width:160px">
      <span class="icon" aria-hidden="true">⌕</span>
      <input id="lib-q" type="search" placeholder="${books ? "Search audiobooks…" : "Search music…"}" title="Press / to search" aria-label="${books ? "Search audiobooks" : "Search music"}"/>
    </div>
    <div class="tabs" role="tablist">
      <button data-t="tracks" class="active" role="tab">Tracks</button>
      <button data-t="artists" role="tab">Artists</button>
      <button data-t="albums" role="tab">Albums</button>
    </div>`;
  root.appendChild(bar);

  const actions = document.createElement("div");
  actions.className = "btn-row";
  actions.style.margin = "0 0 10px";
  actions.innerHTML = `
    <button class="btn sm btn-primary" id="lib-play" title="Play selected (or double-click a row)">▶ Play</button>
    <details class="lib-more">
      <summary class="btn sm" aria-label="More actions">⋯ More</summary>
      <div class="lib-more-menu">
        <button class="btn sm" id="lib-enqueue">＋ Enqueue</button>
        <button class="btn sm" id="lib-addpl" title="Add the selected songs (or all shown) to a playlist">♫ Add to playlist…</button>
        <button class="btn sm" id="lib-folders" aria-expanded="false">📁 Folders</button>
        <button class="btn sm" id="lib-rescan">Rescan</button>
      </div>
    </details>
    <span class="spacer" style="flex:1"></span>
    <span class="muted" id="lib-status"></span>`;
  root.appendChild(actions);
  // Narrow panes fold the secondary actions into a dropdown; close it after a pick or outside click.
  const moreEl = actions.querySelector(".lib-more");
  moreEl.addEventListener("click", (e) => { if (e.target.closest(".lib-more-menu button")) moreEl.open = false; });
  // The document listener outlives the view unless it is aborted when the root is removed.
  const moreAbort = new AbortController();
  document.addEventListener("click", (e) => { if (moreEl.open && !moreEl.contains(e.target)) moreEl.open = false; }, { signal: moreAbort.signal });
  let moreWatch = null;

  // ---- this view's folders (Music or Books), shown on demand or when there are none
  const foldersEl = document.createElement("div");
  foldersEl.className = "lib-folders";
  root.appendChild(foldersEl);
  const foldersBtn = actions.querySelector("#lib-folders");
  let foldersSeq = 0; // a late answer for a closed (or reopened) manager is ignored
  function showFolders(open) {
    const mine = ++foldersSeq;
    foldersBtn.setAttribute("aria-expanded", String(open));
    foldersBtn.classList.toggle("btn-primary", open);
    foldersEl.replaceChildren();
    if (!open) return;
    mountFolders(foldersEl, api, kind, () => {
      lastSig = "folders changed"; // the next poll reloads, even if the scan already finished
      offset = 0; loadTracksAppend();
    }, () => mine === foldersSeq);
  }
  foldersBtn.addEventListener("click", () => showFolders(foldersBtn.getAttribute("aria-expanded") !== "true"));

  const listEl = document.createElement("div");
  listEl.id = "lib-list";
  root.appendChild(listEl);

  const qInput = bar.querySelector("#lib-q");
  const statusEl = actions.querySelector("#lib-status");

  // ---- data loading
  async function loadTracksAppend() {
    const requestOffset = offset;
    const requestQuery = query;
    const generation = ++tracksGeneration;
    const params = new URLSearchParams({ limit: "200", offset: String(requestOffset), kind: apiKind });
    if (requestQuery) params.set("q", requestQuery);
    statusEl.innerHTML = `<span class="spinner"></span>`;
    try {
      const res = await api(`/api/library/tracks?${params}`);
      if (generation !== tracksGeneration) return;
      if (requestQuery !== query || requestOffset !== offset) return;
      if (requestOffset === 0) {
        tracks = res.tracks || [];
        selected.clear();
      } else {
        tracks.push(...(res.tracks || []));
      }
      total = res.total ?? tracks.length;
    } catch (e) {
      if (generation !== tracksGeneration) return;
      if (requestOffset > 0) {
        // A failed "load more" keeps the rows already shown; the button can try again.
        statusEl.textContent = `${tracks.length} / ${total} (couldn't load more)`;
        return;
      }
      listElEmpty(listEl, e.message);
    }
    if (generation !== tracksGeneration) return;
    statusEl.textContent = `${tracks.length} / ${total}`;
    renderList();
  }
  // Every list load takes a ticket: a slow answer for a tab or album the user already
  // left must not paint over the one they are looking at now.
  async function loadArtists() {
    const generation = ++tracksGeneration;
    statusEl.innerHTML = `<span class="spinner"></span>`;
    let rows = [];
    try { rows = await api(`/api/library/artists?kind=${apiKind}`); } catch (e) {
      if (generation !== tracksGeneration) return;
      listElEmpty(listEl, e.message);
    }
    if (generation !== tracksGeneration) return;
    artists = rows;
    statusEl.textContent = `${artists.length} artists`;
    renderList();
  }
  async function loadAlbums(artist = "") {
    const generation = ++tracksGeneration;
    statusEl.innerHTML = `<span class="spinner"></span>`;
    let rows = [];
    try {
      const q = new URLSearchParams({ kind: apiKind });
      if (artist) q.set("artist", artist);
      rows = await api(`/api/library/albums?${q}`);
    } catch (e) {
      if (generation !== tracksGeneration) return;
      listElEmpty(listEl, e.message);
    }
    if (generation !== tracksGeneration) return;
    albums = rows;
    statusEl.textContent = `${albums.length} albums`;
    renderList();
  }

  // ---- renderers
  function listElEmpty(el, msg, big = "♫") {
    el.innerHTML = "";
    const d = document.createElement("div");
    d.className = "empty";
    d.innerHTML = `<span class="big"></span>`;
    d.querySelector("span").textContent = big;
    const p = document.createElement("p");
    p.textContent = msg;
    d.appendChild(p);
    el.appendChild(d);
  }

  function renderList() {
    if (tab === "tracks") return renderTracks();
    if (tab === "artists") return renderArtists();
    if (tab === "albums") return renderAlbums();
  }

  function rowTd(tr, key, cls = "") {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = tr[key] ?? "";
    return td;
  }

  function renderTracks() {
    loadMoreObserver?.disconnect();
    loadMoreObserver = null;
    listEl.innerHTML = "";
    if (!tracks.length) {
      listElEmpty(listEl,
        query ? `No tracks match “${query}”.` :
        books ? "No audiobooks yet. Click 📁 Folders to choose the folder that holds them."
          : "No music yet. Click 📁 Folders to choose where your music is.",
        "📂");
      return;
    }
    const tbl = document.createElement("table");
    tbl.className = books ? "list books" : "list"; // books: a wider Time column for "left off / length"
    tbl.innerHTML = `<thead><tr><th class="num">#</th><th>Title</th><th>Artist</th><th>Album</th><th class="num time">Time</th><th class="like-cell"><span class="sr-only">Liked</span></th></tr></thead>`;
    const tb = document.createElement("tbody");
    const playingIdx = safePlayingIndex();
    tracks.forEach((t, i) => {
      const tr = document.createElement("tr");
      tr.className = "trow";
      tr.tabIndex = 0;
      tr.dataset.i = i;
      if (selected.has(i)) tr.classList.add("selected");
      if (playingIdx === i) tr.classList.add("playing");
      const num = document.createElement("td"); num.className = "num"; num.textContent = i + 1;
      const title = rowTd(t, "title", "title-cell");
      tr.append(num, title, rowTd(t, "artist"), rowTd(t, "album"),
        (() => {
          const td = document.createElement("td"); td.className = "num";
          td.textContent = fmtDur(t.duration);
          if (t.resume > 0) { // a book you started: where you left off, then its length
            const left = document.createElement("span");
            left.className = "left-off";
            left.textContent = `${fmtClock(t.resume)} / `;
            td.prepend(left);
            td.title = `Left off at ${fmtClock(t.resume)}`;
          }
          return td;
        })(), likeCell(t));
      const activate = (mode) => activateRow(i, mode);
      tr.addEventListener("dblclick", () => activate("play"));
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target === tr) activate("play");
        if ((e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) && !pickMode) {
          e.preventDefault();
          if (!selected.has(i)) selectRow(i);
          const rect = tr.getBoundingClientRect();
          openTrackMenu(i, { x: rect.left, y: rect.bottom });
        }
      });
      tr.addEventListener("click", (e) => selectRow(i, e));
      tr.addEventListener("contextmenu", (e) => {
        if (pickMode) return;
        e.preventDefault();
        if (!selected.has(i)) selectRow(i);
        openTrackMenu(i, { x: e.clientX, y: e.clientY });
      });
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    listEl.appendChild(tbl);
    if (tracks.length < total) {
      const more = document.createElement("div");
      more.style.cssText = "text-align:center;padding:10px";
      const b = document.createElement("button");
      b.className = "btn";
      b.textContent = `Load more (${total - tracks.length} left)`;
      let requested = false;
      const loadNext = () => {
        if (requested) return;
        requested = true;
        loadMoreObserver?.disconnect();
        offset = tracks.length;
        loadTracksAppend();
      };
      // Scrolling into the marker is the normal path. The button remains a
      // keyboard/accessibility fallback when IntersectionObserver is absent.
      b.addEventListener("click", loadNext);
      more.appendChild(b);
      listEl.appendChild(more);
      if (typeof IntersectionObserver === "function") {
        loadMoreObserver = new IntersectionObserver((entries) => {
          if (entries.some((entry) => entry.isIntersecting)) loadNext();
        }, { root: listEl.closest(".panel-body"), rootMargin: "0px 0px 240px" });
        loadMoreObserver.observe(more);
      }
    }
  }

  function renderArtists() {
    listEl.innerHTML = "";
    if (!artists.length) return listElEmpty(listEl, "No artists yet — rescan after adding folders.", "🎤");
    const tbl = document.createElement("table"); tbl.className = "list";
    const tb = document.createElement("tbody");
    for (const a of artists) {
      const tr = document.createElement("tr");
      tr.className = "trow";
      tr.tabIndex = 0;
      tr.innerHTML = `<td>${esc(a.name)}</td>`;
      const tdN = document.createElement("td"); tdN.className = "num";
      tdN.textContent = `${a.tracks} tracks · ${a.albums} albums`;
      tr.appendChild(tdN);
      const open = () => {
        query = "";
        qInput.value = a.name;
        tab = "albums";
        setTabs();
        loadAlbums(a.name);
      };
      const openMenu = (point) => openArtistMenu(a.name, point, tr);
      const consumed = bindLongPress(tr, (point) => openMenu(point));
      tr.addEventListener("click", () => { if (consumed()) return; open(); });
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        } else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
          e.preventDefault();
          const rect = tr.getBoundingClientRect();
          openMenu({ x: rect.left, y: rect.bottom });
        }
      });
      tr.addEventListener("contextmenu", (e) => {
        if (pickMode) return;
        e.preventDefault();
        openMenu({ x: e.clientX, y: e.clientY });
      });
      tr.appendChild(moreCell(`Actions for ${a.name}`, (point) => openMenu(point)));
      tb.appendChild(tr);
    }
    tbl.appendChild(tb); listEl.appendChild(tbl);
  }

  function renderAlbums() {
    listEl.innerHTML = "";
    if (currentAlbum) return renderAlbumTracks();
    if (!albums.length) return listElEmpty(listEl, "No albums yet.", "💿");
    const grid = document.createElement("div"); grid.className = "album-grid";
    for (const al of albums) {
      const card = document.createElement("div");
      card.className = "album-card";
      card.tabIndex = 0;
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = al.album || "Untitled";
      if (al.art_track_id) img.src = api.authedUrl(`/api/art/${al.art_track_id}`);
      const nm = document.createElement("div"); nm.className = "a-name"; nm.textContent = al.album || "Untitled";
      const ar = document.createElement("div"); ar.className = "a-artist";
      ar.textContent = [al.artist, al.year].filter(Boolean).join(" · ") + ` · ${al.tracks} tracks`;
      card.append(img, nm, ar);
      const open = () => { currentAlbum = al; renderAlbums(); };
      const openMenu = (point) => openAlbumMenu(al, point, card);
      const consumed = bindLongPress(card, (point) => openMenu(point));
      card.addEventListener("click", () => { if (consumed()) return; open(); });
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter") open();
        else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
          e.preventDefault();
          const rect = card.getBoundingClientRect();
          openMenu({ x: rect.left, y: rect.bottom });
        }
      });
      card.addEventListener("contextmenu", (e) => {
        if (pickMode) return;
        e.preventDefault();
        openMenu({ x: e.clientX, y: e.clientY });
      });
      card.appendChild(moreButton(`Actions for ${al.album || "Untitled"}`, (point) => openMenu(point)));
      grid.appendChild(card);
    }
    listEl.appendChild(grid);
  }

  async function renderAlbumTracks() {
    const generation = ++tracksGeneration;
    listEl.innerHTML = `<div style="padding:20px;text-align:center"><span class="spinner"></span></div>`;
    try {
      const res = await api(`/api/library/tracks?${new URLSearchParams({ album: currentAlbum.album, artist: currentAlbum.artist || "", kind: apiKind, limit: "200", offset: "0" })}`);
      if (generation !== tracksGeneration) return;
      tracks = res.tracks || [];
      selected.clear();
      total = res.total ?? tracks.length;
      offset = 0;
      tab = "tracks"; renderTracks();
      const back = document.createElement("button");
      back.className = "btn btn-ghost sm"; back.textContent = "← All albums"; back.style.margin = "6px 0";
      back.addEventListener("click", () => { currentAlbum = null; tab = "albums"; renderList(); });
      listEl.prepend(back);
    } catch (e) { if (generation === tracksGeneration) listElEmpty(listEl, e.message); }
  }

  // ---- likes: a heart per track; the player owns the set (shared with the Hermes mini player)
  function likeCell(t) {
    const td = document.createElement("td");
    td.className = "like-cell";
    const b = document.createElement("button");
    b.type = "button";
    b.className = "like-btn";
    b.dataset.id = t.id;
    paintLike(b, t);
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      window.revampPlayerApi?.setLiked?.(t.id, b.getAttribute("aria-pressed") !== "true");
    });
    b.addEventListener("dblclick", (e) => e.stopPropagation());
    b.addEventListener("keydown", (e) => e.stopPropagation());
    td.appendChild(b);
    return td;
  }
  function paintLike(b, t) {
    const pa = window.revampPlayerApi;
    const on = pa?.isLiked ? pa.isLiked(b.dataset.id) : !!t?.liked;
    b.setAttribute("aria-pressed", String(on));
    b.textContent = on ? "♥" : "♡";
    b.title = on ? "Unlike" : "Like";
    b.setAttribute("aria-label", `${on ? "Unlike" : "Like"}: ${t?.title || ""}`);
  }
  const onLikes = () => {
    if (!document.body.contains(listEl)) return window.removeEventListener("revamp:likes", onLikes);
    listEl.querySelectorAll(".like-btn").forEach((b) => paintLike(b, tracks.find((x) => x.id === b.dataset.id)));
  };
  window.addEventListener("revamp:likes", onLikes);

  // ---- selection & activation
  function selectRow(i, e) {
    if (e?.shiftKey) {
      const last = [...selected].pop() ?? i;
      for (let k = Math.min(last, i); k <= Math.max(last, i); k++) selected.add(k);
    } else if (e?.ctrlKey || e?.metaKey) {
      selected.has(i) ? selected.delete(i) : selected.add(i);
    } else {
      selected.clear(); selected.add(i);
    }
    renderList();
  }
  function chosenTracks() {
    if (selected.size) return [...selected].sort((a, b) => a - b).map((i) => tracks[i]).filter(Boolean);
    return tracks.slice();
  }
  function activateRow(i, mode) {
    if (pickMode && pickCb) {
      selected.add(i);
      const list = [...selected].sort((a, b) => a - b).map((k) => tracks[k]).filter(Boolean);
      const cb = pickCb; pickMode = false; pickCb = null; selected.clear();
      cb(list.map(trackToItem));
      return;
    }
    // A multi-selection containing the row queues just the selection; otherwise the whole result set.
    const order = selected.size > 1 && selected.has(i) ? chosenTracks() : tracks.slice();
    const items = order.map(trackToItem);
    const startIdx = order.indexOf(tracks[i]);
    // One call that loads, then plays the chosen row (like Playlists): from the Hermes library
    // these were two server commands that could land in either order, so a book's continue
    // was dropped, or undone by the late "play row" (Grok a10r14).
    const p = window.revampPlayerApi;
    if (p.playItemsAt) return p.playItemsAt(Math.max(0, startIdx), items);
    p.loadTracks(items, { mode: "replace", play: false }).then(() => p.playIndex(Math.max(0, startIdx)));
  }

  // ---- one context menu for every row: track rows, album cards and artist rows.
  // Right-click, long-press, the ⋯ button, Shift+F10 and the context-menu key all open it.
  let itemMenu = null;
  let itemMenuCleanup = null;
  // A menu opens only if its own open request is still the newest (a slow album fetch must not
  // paint over a menu the user opened later).
  let itemMenuSeq = 0;

  function closeItemMenu(returnFocus = false) {
    if (!itemMenu) return;
    const { element, focusEl } = itemMenu;
    itemMenu = null;
    itemMenuCleanup?.();
    itemMenuCleanup = null;
    element.remove();
    if (returnFocus) focusEl?.focus();
  }

  // A small ⋯ button on cards and rows: opens the same menu where the button sits.
  function moreButton(label, onOpen) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "album-more";
    button.textContent = "⋯";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.addEventListener("click", (e) => {
      e.stopPropagation();
      const rect = button.getBoundingClientRect();
      onOpen({ x: rect.left, y: rect.bottom });
    });
    return button;
  }
  function moreCell(label, onOpen) {
    const td = document.createElement("td");
    td.className = "more-cell";
    td.appendChild(moreButton(label, onOpen));
    return td;
  }

  // Long-press on a touch screen opens the menu; the click that follows is swallowed so the
  // card does not also open. Returns a function that tells the click handler to ignore it.
  function bindLongPress(el, onLong) {
    let timer = null;
    let fired = false;
    let sx = 0, sy = 0;
    const cancel = () => { clearTimeout(timer); timer = null; };
    el.addEventListener("touchstart", (e) => {
      if (pickMode || !e.touches || e.touches.length !== 1) return;
      sx = e.touches[0].clientX; sy = e.touches[0].clientY; fired = false;
      cancel();
      timer = setTimeout(() => { fired = true; onLong({ x: sx, y: sy }); }, 500);
    }, { passive: true });
    el.addEventListener("touchmove", (e) => {
      const t = e.touches?.[0];
      if (!t) return;
      if (Math.abs(t.clientX - sx) > 10 || Math.abs(t.clientY - sy) > 10) cancel();
    }, { passive: true });
    el.addEventListener("touchend", (e) => { cancel(); if (fired) e.preventDefault(); }, { passive: false });
    el.addEventListener("touchcancel", cancel, { passive: true });
    return () => { const was = fired; fired = false; return was; };
  }

  // Every page of a filtered list, in the server's order (album: disc then track number).
  async function fetchAllTracks(params) {
    const out = [];
    for (;;) {
      const q = new URLSearchParams({ ...params, limit: "500", offset: String(out.length) });
      const res = await api(`/api/library/tracks?${q}`);
      const rows = res.tracks || [];
      out.push(...rows);
      if (!rows.length || out.length >= (res.total ?? out.length) || out.length > 50000) break;
    }
    return out;
  }

  async function openAlbumMenu(al, point, focusEl) {
    const seq = ++itemMenuSeq;
    let tracks;
    try {
      tracks = await fetchAllTracks({ album: al.album || "", artist: al.artist || "", kind: apiKind, sort: "album" });
    } catch (e) {
      return toast(e.message, { kind: "error" });
    }
    if (seq !== itemMenuSeq) return; // a newer menu was opened while this one loaded
    const items = tracks.map(trackToItem);
    if (!items.length) return toast("That album has no tracks yet", { kind: "error" });
    openItemMenu({
      items, point, focusEl, withLike: false,
      label: "Album actions",
      headerText: `${al.album || "Untitled"} · ${items.length} ${items.length === 1 ? "track" : "tracks"}`,
    });
  }

  async function openArtistMenu(name, point, focusEl) {
    const seq = ++itemMenuSeq;
    let tracks;
    try {
      tracks = await fetchAllTracks({ artist: name || "", kind: apiKind, sort: "album" });
    } catch (e) {
      return toast(e.message, { kind: "error" });
    }
    if (seq !== itemMenuSeq) return; // a newer menu was opened while this one loaded
    const items = tracks.map(trackToItem);
    if (!items.length) return toast(`No tracks by ${name || "this artist"}`, { kind: "error" });
    openItemMenu({
      items, point, focusEl, withLike: false,
      label: "Artist actions",
      headerText: `${name || "Unknown Artist"} · ${items.length} ${items.length === 1 ? "track" : "tracks"}`,
    });
  }

  function openTrackMenu(rowIndex, point) {
    itemMenuSeq += 1; // cancels an album/artist menu still loading
    const items = chosenTracks().map(trackToItem);
    openItemMenu({
      items, point, withLike: true,
      focusEl: listEl.querySelector(`tr[data-i="${rowIndex}"]`),
      label: "Track actions",
      headerText: `${items.length} ${items.length === 1 ? "song" : "songs"}`,
    });
  }

  // One menu implementation for tracks, albums and artists: Play, Play next, Add to queue,
  // Add to playlist ▸ (saved playlists + New playlist…), plus Like for single tracks.
  function openItemMenu({ items, point, focusEl, label, headerText, withLike }) {
    itemMenuSeq += 1;
    closeItemMenu();
    const menu = document.createElement("div");
    menu.className = "track-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", label || "Actions");
    const header = document.createElement("div");
    header.className = "track-menu-header";
    header.textContent = headerText || `${items.length} tracks`;
    menu.appendChild(header);
    const addItem = (text, action, opts = {}) => {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "menuitem");
      if (opts.haspopup) {
        button.setAttribute("aria-haspopup", "menu");
        button.setAttribute("aria-expanded", "false");
      }
      button.textContent = text;
      button.addEventListener("click", (e) => {
        e.stopPropagation();
        action(button);
      });
      menu.appendChild(button);
      return button;
    };
    const run = (fn) => () => { closeItemMenu(); fn(); };
    addItem("Play", run(() => playItemsNow(items)));
    addItem("Play next", run(() => queueItems(items, "next")));
    addItem("Add to queue", run(() => queueItems(items, "append")));
    if (withLike) {
      const allLiked = chosenTracks().every((track) => {
        const player = window.revampPlayerApi;
        return player?.isLiked ? player.isLiked(track.id) : !!track.liked;
      });
      addItem(allLiked ? "Unlike" : "Like", run(() => setChosenLiked(!allLiked)));
    }

    // Add to playlist ▸: saved playlists plus a new one. The list is fetched when the
    // submenu first opens, so the menu itself never waits on the server.
    const plButton = addItem("Add to playlist ▸", () => openSub(true), { haspopup: true });
    const sub = document.createElement("div");
    sub.className = "track-menu-sub";
    sub.setAttribute("role", "group");
    sub.setAttribute("aria-label", "Playlists");
    sub.hidden = true;
    menu.appendChild(sub);
    let subLoaded = false;
    let subLoading = null;
    const addSubItem = (text, action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "menuitem");
      button.textContent = text;
      button.addEventListener("click", (e) => {
        e.stopPropagation();
        action();
      });
      sub.appendChild(button);
      return button;
    };
    const fillSub = async () => {
      let pls = [];
      try {
        pls = await api("/api/playlists");
      } catch (e) {
        toast(e.message, { kind: "error" });
        subLoaded = false;
        subLoading = null; // a failed fetch can be tried again on the next open
        return;
      }
      sub.replaceChildren();
      for (const p of pls || []) addSubItem(p.name || "Untitled", () => saveItemsToPlaylist(items, p.id));
      addSubItem("＋ New playlist…", () => newPlaylistWith(items));
      subLoaded = true;
    };
    const openSub = async (focusFirst = false) => {
      sub.hidden = false;
      plButton.setAttribute("aria-expanded", "true");
      if (!subLoaded) subLoading = subLoading || fillSub();
      await subLoading;
      if (focusFirst) sub.querySelector('[role="menuitem"]')?.focus();
    };
    const closeSub = () => {
      sub.hidden = true;
      plButton.setAttribute("aria-expanded", "false");
    };

    root.appendChild(menu);

    const rect = menu.getBoundingClientRect();
    const x = Math.max(8, Math.min(point.x, window.innerWidth - rect.width - 8));
    const y = Math.max(8, Math.min(point.y, window.innerHeight - rect.height - 8));
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    itemMenu = { element: menu, focusEl };

    // Only visible items are reachable with the arrow keys: a closed submenu's rows are not.
    const menuItems = () => [...menu.querySelectorAll('[role="menuitem"]')]
      .filter((button) => button.parentNode !== sub || !sub.hidden);
    const onKeydown = (e) => {
      const buttons = menuItems();
      const current = buttons.indexOf(document.activeElement);
      if (e.key === "Escape") {
        e.preventDefault();
        closeItemMenu(true);
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!buttons.length) return;
        const direction = e.key === "ArrowDown" ? 1 : -1;
        buttons[(current + direction + buttons.length) % buttons.length].focus();
      } else if (e.key === "ArrowRight" && document.activeElement === plButton) {
        e.preventDefault();
        openSub(true);
      } else if (e.key === "ArrowLeft" && sub.contains(document.activeElement)) {
        e.preventDefault();
        closeSub();
        plButton.focus();
      } else if (e.key === "Enter" && current >= 0) {
        e.preventDefault();
        buttons[current].click();
      }
    };
    const onOutsideClick = (e) => {
      if (!menu.contains(e.target)) closeItemMenu();
    };
    let opening = true;
    const onScroll = () => {
      if (!opening) closeItemMenu();
    };
    menu.querySelector('[role="menuitem"]')?.focus();
    document.addEventListener("keydown", onKeydown);
    document.addEventListener("click", onOutsideClick);
    document.addEventListener("scroll", onScroll, true);
    itemMenuCleanup = () => {
      document.removeEventListener("keydown", onKeydown);
      document.removeEventListener("click", onOutsideClick);
      document.removeEventListener("scroll", onScroll, true);
    };
    setTimeout(() => {
      opening = false;
    }, 200);
  }

  function playItemsNow(items) {
    if (!items.length) return;
    const p = window.revampPlayerApi;
    if (p.playItemsAt) return p.playItemsAt(0, items);
    p.loadTracks(items, { mode: "replace", play: true });
  }

  function queueItems(items, mode) {
    if (!items.length) return;
    const n = items.length;
    const what = `${n} ${n === 1 ? "track" : "tracks"}`;
    window.revampPlayerApi.loadTracks(items, { mode });
    toast(mode === "next" ? `${what} will play next` : `Added ${what} to the queue`, { kind: "ok" });
  }

  async function saveItemsToPlaylist(items, id) {
    closeItemMenu();
    try {
      const cur = await api(`/api/playlists/${encodeURIComponent(id)}`);
      await api(`/api/playlists/${encodeURIComponent(id)}`, "PUT", { items: [...(cur.items || []), ...items] });
      const n = items.length;
      toast(`Added ${n} ${n === 1 ? "track" : "tracks"} to “${cur.name}”`, { kind: "ok" });
      window.dispatchEvent(new Event("revamp:playlists-changed"));
    } catch (e) {
      toast(`Could not save playlist: ${e.message}`, { kind: "error" });
    }
  }

  async function newPlaylistWith(items) {
    closeItemMenu();
    const name = await textPrompt("New playlist", { placeholder: "My playlist" });
    if (!name) return;
    try {
      await api("/api/playlists", "POST", { name, items });
      const n = items.length;
      toast(`Created “${name}” with ${n} ${n === 1 ? "track" : "tracks"}`, { kind: "ok" });
      window.dispatchEvent(new Event("revamp:playlists-changed"));
    } catch (e) {
      toast(`Could not save playlist: ${e.message}`, { kind: "error" });
    }
  }

  function playChosenTracks() {
    playItemsNow(chosenTracks().map(trackToItem));
  }

  function enqueueChosenTracks() {
    queueItems(chosenTracks().map(trackToItem), "append");
  }

  function setChosenLiked(liked) {
    chosenTracks().forEach((track) => window.revampPlayerApi?.setLiked?.(track.id, liked));
  }

  async function addChosenTracksToPlaylist() {
    const items = chosenTracks().map(trackToItem);
    if (!items.length) return;
    let pls = [];
    try {
      pls = await api("/api/playlists");
    } catch (e) {
      return toast(e.message, { kind: "error" });
    }
    const body = document.createElement("div");
    body.className = "btn-col";
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = selected.size ? `${items.length} selected song(s)` : `All ${items.length} songs shown (select rows to add just some)`;
    body.appendChild(note);
    const pick = await openModal({
      title: "Add to playlist",
      body,
      actions: [
        { label: "＋ New playlist…", value: "__new__", primary: true },
        ...pls.map((p) => ({ label: p.name, value: p.id })),
        { label: "Cancel", value: null },
      ],
    });
    if (!pick) return;
    if (pick === "__new__") return newPlaylistWith(items);
    return saveItemsToPlaylist(items, pick);
  }

  // action buttons
  {
    actions.querySelector("#lib-play").addEventListener("click", () => {
      playChosenTracks();
    });
    actions.querySelector("#lib-enqueue").addEventListener("click", () => {
      enqueueChosenTracks();
    });
    actions.querySelector("#lib-addpl").addEventListener("click", addChosenTracksToPlaylist);
    actions.querySelector("#lib-rescan").addEventListener("click", async () => {
      try { await api("/api/library/rescan", "POST"); statusEl.textContent = "scan started"; } catch (e) { statusEl.textContent = e.message; }
    });
  }

  // tabs + search wiring
  function setTabs() {
    bar.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.t === tab));
  }
  bar.querySelectorAll(".tabs button").forEach((b) =>
    b.addEventListener("click", () => {
      tab = b.dataset.t; setTabs(); selected.clear(); offset = 0;
      currentAlbum = null; // any tab leaves an open album
      if (tab === "tracks") loadTracksAppend();
      if (tab === "artists") loadArtists();
      if (tab === "albums") loadAlbums();
    }));
  let debounceT;
  qInput.addEventListener("input", () => {
    clearTimeout(debounceT);
    debounceT = setTimeout(() => {
      query = qInput.value.trim(); offset = 0; selected.clear();
      currentAlbum = null; // a search is over the whole library
      if (tab !== "tracks") { tab = "tracks"; setTabs(); }
      loadTracksAppend();
    }, 200);
  });

  // Scan poll (only while visible): show progress, and reload the list as songs
  // arrive and when the scan ends — folders change from the Hermes status bar too.
  let lastSig = null; // track count + when the last scan finished
  let polling = false; // one poll (and reload) at a time: an older reload can't overwrite a newer one
  const scanPoll = setInterval(async () => {
    if (!document.body.contains(statusEl)) {
      moreAbort.abort();
      moreWatch?.disconnect();
      loadMoreObserver?.disconnect();
      return clearInterval(scanPoll);
    }
    if (document.hidden) return;
    if (polling) return;
    polling = true;
    try {
      const st = await api("/api/library/status");
      const sig = `${st.count}|${st.last_scan}|${st.scanning}`;
      const changed = lastSig !== null && sig !== lastSig;
      lastSig = sig;
      if (changed && currentAlbum) { /* an open album stays as it is */ }
      else if (changed && tab === "tracks" && offset === 0) await loadTracksAppend();
      else if (changed && tab !== "tracks") await (tab === "artists" ? loadArtists() : loadAlbums());
      if (st.scanning) {
        const p = st.progress || {};
        statusEl.innerHTML = `<span class="spinner"></span> scanning… ${p.total ? `${p.done.toLocaleString()} / ${p.total.toLocaleString()} files` : ""}`;
      }
    } catch { /* offline */ } finally { polling = false; }
  }, 3000);
  moreWatch = new MutationObserver(() => {
    if (!root.isConnected) {
      moreAbort.abort();
      moreWatch.disconnect();
      loadMoreObserver?.disconnect();
      clearInterval(scanPoll);
    }
  });
  if (root.parentNode) moreWatch.observe(root.parentNode, { childList: true });

  // initial load
  const status0 = await api("/api/library/status").catch(() => null);
  if (status0) lastSig = `${status0.count}|${status0.last_scan}|${status0.scanning}`;
  if (status0?.scanning) statusEl.innerHTML = `<span class="spinner"></span> scanning…`;
  loadTracksAppend();
  // No folders for this view yet: open the folder manager straight away.
  api("/api/library/folders").then((res) => {
    const want = books ? "books" : "music";
    // ...unless the user already opened or closed it themselves.
    if (foldersSeq === 0 && !(res.folders || []).some((f) => (f.kind === "books" ? "books" : "music") === want)) showFolders(true);
  }).catch(() => {});
}

function safePlayingIndex() { return -1; }
function fmtDur(s) { if (!Number.isFinite(s)) return "--:--"; s = Math.floor(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
function esc(s) { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; }
