// playlists.js — list, open (items), play/append, rename/delete, save current queue,
// import m3u/pls (file input), export links.
import { confirmDialog, textPrompt, toast } from "./modal.js";

const LIKED = "__liked__"; // the editor's id for the Liked list (never a real playlist id)

// Any view that creates or edits a playlist fires this; the open list reloads.
let refreshLive = null;
window.addEventListener("revamp:playlists-changed", () => { if (refreshLive && document.contains(refreshLive.el)) refreshLive.fn(); });

export async function render(root, api) {
  // On the control page there is no local player: send the same commands to the live one.
  const player = () => window.revampPlayerApi || {
    loadTracks: (items, o = {}) => api("/api/command", "POST", { action: "load", items, mode: o.mode || "replace", play: o.play !== false }),
    playIndex: (index) => api("/api/command", "POST", { action: "play_index", index }),
  };
  root.innerHTML = "";
  const bar = document.createElement("div");
  bar.className = "btn-row";
  bar.style.marginBottom = "12px";
  bar.innerHTML = `
    <button class="btn sm btn-primary" id="pl-new">＋ New playlist</button>
    <button class="btn sm" id="pl-saveq">⬇ Save current queue</button>
    <button class="btn sm" id="pl-import">⬆ Import…</button>
    <input type="file" id="pl-file" accept=".m3u,.m3u8,.pls" hidden />`;
  root.appendChild(bar);

  const listEl = document.createElement("div");
  listEl.className = "card-list";
  root.appendChild(listEl);
  const detail = document.createElement("div");
  root.appendChild(detail);

  refreshLive = { el: listEl, fn: () => refresh() };
  async function refresh() {
    listEl.innerHTML = `<div style="padding:16px"><span class="spinner"></span></div>`;
    let pls = [];
    try { pls = await api("/api/playlists"); } catch (e) { return errBox(listEl, e); }
    listEl.innerHTML = "";
    listEl.appendChild(await likedRow());
    if (!pls.length) {
      const d = document.createElement("div");
      d.className = "empty";
      d.innerHTML = `<span class="big">≡</span>No playlists yet. Save the current queue or import an .m3u/.pls file.`;
      listEl.appendChild(d);
      return;
    }
    for (const pl of pls) {
      const row = document.createElement("div");
      row.className = "card-row";
      row.tabIndex = 0;
      const grow = document.createElement("div"); grow.className = "grow";
      const nm = document.createElement("div"); nm.className = "name"; nm.textContent = pl.name;
      const sub = document.createElement("div"); sub.className = "sub";
      sub.textContent = `${pl.count} items · ${fmtDur(pl.duration)}`;
      grow.append(nm, sub);
      const btns = document.createElement("div"); btns.className = "btn-row";
      const mkBtn = (label, name, fn, cls = "") => {
        const b = document.createElement("button"); b.className = "btn sm " + cls; b.textContent = label;
        b.setAttribute("aria-label", name);
        b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
        // Enter on a nested button must not also trigger the row's open handler.
        b.addEventListener("keydown", (e) => { if (e.key === "Enter") e.stopPropagation(); });
        return b;
      };
      const bPlay = mkBtn("▶", "Play playlist", () => play(pl.id));
      const bAdd = mkBtn("＋", "Add playlist to queue", () => append(pl.id));
      const bRen = mkBtn("✎", "Rename playlist", () => rename(pl));
      const bDel = mkBtn("🗑", "Delete playlist", () => del(pl), "btn-danger");
      const bExpM = mkBtn(".m3u8", "Export playlist as M3U8", () => exportPl(pl.id, "m3u8"));
      const bExpP = mkBtn(".pls", "Export playlist as PLS", () => exportPl(pl.id, "pls"));
      btns.append(bPlay, bAdd, bRen, bDel, bExpM, bExpP);
      row.append(grow, btns);
      const open = () => openPl(pl);
      row.addEventListener("click", open);
      row.addEventListener("keydown", (e) => e.key === "Enter" && open());
      listEl.appendChild(row);
    }
  }

  // "Liked": every track you gave a heart, oldest like first. Not a file: it follows the likes.
  async function likedItems() {
    const res = await api("/api/likes");
    return (res.tracks || []).map((t) => ({
      kind: "track", title: t.title, artist: t.artist || "", album: t.album || "",
      url: t.url, duration: t.duration ?? null, track_id: t.id,
      ...(t.kind ? { book: t.kind === "audiobook" } : {}),
    }));
  }
  async function likedRow() {
    let items = [];
    try { items = await likedItems(); } catch { /* older server: still show the row */ }
    const row = document.createElement("div");
    row.className = "card-row";
    row.id = "pl-liked";
    row.tabIndex = 0;
    const grow = document.createElement("div"); grow.className = "grow";
    const nm = document.createElement("div"); nm.className = "name"; nm.textContent = "♥ Liked";
    const sub = document.createElement("div"); sub.className = "sub";
    sub.textContent = items.length ? `${items.length} items · ${fmtDur(items.reduce((a, it) => a + (it.duration || 0), 0))}` : "Tap ♡ on a track to add it here";
    grow.append(nm, sub);
    const btns = document.createElement("div"); btns.className = "btn-row";
    const mk = (label, fn) => {
      const b = document.createElement("button"); b.className = "btn sm"; b.textContent = label;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      return b;
    };
    const playAll = async () => {
      const list = await likedItems().catch(() => []);
      if (list.length) player().loadTracks(list, { mode: "replace", play: true });
    };
    const addAll = async () => {
      const list = await likedItems().catch(() => []);
      if (list.length) { player().loadTracks(list, { mode: "append" }); toast(`Appended ${list.length} items`, { kind: "ok" }); }
    };
    // A normal copy you can edit freely; the Liked list itself keeps following your hearts.
    const saveAs = async () => {
      const list = await likedItems().catch(() => []);
      if (!list.length) return toast("Nothing liked yet", { kind: "info" });
      const name = await textPrompt("Save Liked as a playlist", { value: "Liked songs" });
      if (!name) return;
      try {
        await api("/api/playlists", "POST", { name, items: list });
        toast(`Saved “${name}” (${list.length} items)`, { kind: "ok" });
        refresh();
      } catch (e) { toast(`Could not save playlist: ${e.message}`, { kind: "error" }); }
    };
    const b1 = mk("▶", playAll), b2 = mk("＋", addAll), b3 = mk("⬇", saveAs);
    b1.title = "Play all"; b2.title = "Add all to the queue"; b3.title = "Save as a playlist";
    btns.append(b1, b2, b3);
    row.append(grow, btns);
    const open = () => openLiked();
    row.addEventListener("click", open);
    row.addEventListener("keydown", (e) => e.key === "Enter" && open());
    return row;
  }

  // Liked opens in the same editor; removing a row un-hearts it, moving reorders the hearts.
  async function openLiked() {
    return openPl({ id: LIKED }, { id: LIKED, name: "♥ Liked", items: await likedItems().catch(() => []) });
  }

  async function openPl(pl, preloaded) {
    let data = preloaded;
    if (!data) try { data = await api(`/api/playlists/${pl.id}`); } catch (e) { return toast(e.message, { kind: "error" }); }
    detail.innerHTML = "";
    detail.dataset.pid = pl.id; // which playlist is open (delete only closes that one)
    const heading = document.createElement("div");
    heading.className = "btn-row";
    heading.style.margin = "14px 0 8px";
    const h = document.createElement("h3");
    h.textContent = `${data.name} — ${data.items.length} items`;
    h.style.margin = "0";
    const hint = document.createElement("span");
    hint.className = "muted";
    hint.textContent = data.id === LIKED ? "Removing a song here un-hearts it" : "Edits save immediately";
    heading.append(h, hint);
    const tbl = document.createElement("table"); tbl.className = "list";
    const tb = document.createElement("tbody");
    const editButtons = [];
    let savingItems = false;
    data.items.forEach((it, i) => {
      const tr = document.createElement("tr"); tr.className = "trow"; tr.tabIndex = 0;
      const n = document.createElement("td"); n.className = "num"; n.textContent = i + 1;
      const t = document.createElement("td"); t.textContent = it.title; t.className = "title-cell";
      const a = document.createElement("td"); a.textContent = it.artist || (it.kind === "stream" ? "stream" : "");
      const d = document.createElement("td"); d.className = "num"; d.textContent = fmtDur(it.duration);
      const actions = document.createElement("td"); actions.className = "btn-row";
      const action = (label, title, fn, cls = "") => {
        const b = document.createElement("button");
        b.className = "btn sm " + cls; b.textContent = label; b.title = title;
        b.setAttribute("aria-label", title);
        b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
        b.addEventListener("keydown", (e) => e.stopPropagation());
        editButtons.push(b);
        return b;
      };
      if (i > 0) actions.appendChild(action("↑", "Move up", () => moveItem(i, i - 1)));
      if (i < data.items.length - 1) actions.appendChild(action("↓", "Move down", () => moveItem(i, i + 1)));
      actions.appendChild(action("×", data.id === LIKED ? "Unlike" : "Remove from playlist", () => removeItem(i), "btn-danger"));
      tr.append(n, t, a, d, actions);
      tr.addEventListener("dblclick", () => playFrom(data.items, i));
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target === tr) playFrom(data.items, i);
      });
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    detail.append(heading, tbl);
    detail.scrollIntoView({ behavior: "smooth", block: "nearest" });

    async function replaceItems(items) {
      // Lock row actions for the short save round-trip.  Otherwise two rapid
      // clicks both calculate from this render's old data.items and the later
      // request can silently overwrite the earlier edit.
      if (savingItems) return;
      savingItems = true;
      editButtons.forEach((button) => { button.disabled = true; });
      // What this editor showed: the server removes only likes taken out of it, never ones it
      // didn't show (a rescan in progress hides liked songs from the list).
      const shown = (data.items || []).map((it) => it.track_id);
      try {
        if (data.id === LIKED) {
          await api("/api/likes", "PUT", { ids: items.map((it) => it.track_id), shown });
          await refresh();
          await openLiked();
        } else {
          await api(`/api/playlists/${data.id}`, "PUT", { items });
          await refresh();
          await openPl({ id: data.id });
        }
        data.items = items;
      } catch (e) {
        toast(`Could not save playlist: ${e.message}`, { kind: "error" });
        editButtons.forEach((button) => { button.disabled = false; });
      } finally { savingItems = false; }
    }
    function moveItem(from, to) {
      const items = [...data.items];
      items.splice(to, 0, items.splice(from, 1)[0]);
      replaceItems(items);
    }
    function removeItem(index) { replaceItems(data.items.filter((_, n) => n !== index)); }
  }

  async function play(id) {
    try {
      const data = await api(`/api/playlists/${id}`);
      player().loadTracks(data.items, { mode: "replace", play: true });
    } catch (e) { toast(e.message, { kind: "error" }); }
  }
  async function playFrom(items, i) {
    player().loadTracks(items, { mode: "replace", play: false });
    player().playIndex(i);
  }
  async function append(id) {
    try {
      const data = await api(`/api/playlists/${id}`);
      player().loadTracks(data.items, { mode: "append" });
      toast(`Appended ${data.items.length} items`, { kind: "ok" });
    } catch (e) { toast(e.message, { kind: "error" }); }
  }
  async function rename(pl) {
    const name = await textPrompt("Rename playlist", { value: pl.name });
    if (!name || name === pl.name) return;
    try { await api(`/api/playlists/${pl.id}`, "PUT", { name }); refresh(); } catch (e) { toast(e.message, { kind: "error" }); }
  }
  async function del(pl) {
    if (!(await confirmDialog(`Delete playlist “${pl.name}”?`, { title: "Delete playlist" }))) return;
    try {
      await api(`/api/playlists/${pl.id}`, "DELETE");
      if (detail.dataset.pid === pl.id) { detail.innerHTML = ""; delete detail.dataset.pid; }
      refresh();
    } catch (e) { toast(e.message, { kind: "error" }); }
  }
  function exportPl(id, format) {
    const a = document.createElement("a");
    a.href = api.authedUrl(`/api/playlists/${id}/export`, { format });
    a.download = "";
    a.click();
  }

  bar.querySelector("#pl-new").addEventListener("click", async () => {
    const name = await textPrompt("New playlist", { placeholder: "My playlist" });
    if (!name) return;
    try {
      const pl = await api("/api/playlists", "POST", { name, items: [] });
      toast(`Created “${name}”. Add songs from Music with “Add to playlist”.`, { kind: "ok" });
      await refresh();
      if (pl?.id) openPl({ id: pl.id });
    } catch (e) { toast(`Could not create playlist: ${e.message}`, { kind: "error" }); }
  });

  // save current queue
  bar.querySelector("#pl-saveq").addEventListener("click", () => window.revampPlayerApi?.openSaveQueueModal?.());

  // import
  const fileInput = bar.querySelector("#pl-file");
  bar.querySelector("#pl-import").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const f = fileInput.files[0];
    if (!f) return;
    const text = await f.text();
    const format = /\.pls$/i.test(f.name) ? "pls" : "m3u";
    const name = f.name.replace(/\.(m3u8?|pls)$/i, "");
    try {
      await api("/api/playlists/import", "POST", { name, format, text });
      toast(`Imported “${name}”`, { kind: "ok" });
      refresh();
    } catch (e) { toast(`Import failed: ${e.message}`, { kind: "error" }); }
    fileInput.value = "";
  });

  refresh();
}

function errBox(el, e) {
  el.innerHTML = "";
  const d = document.createElement("div"); d.className = "empty";
  d.textContent = `Failed to load playlists: ${e.message || e}`;
  el.appendChild(d);
}
function fmtDur(s) {
  if (!Number.isFinite(s)) return "–:––";
  s = Math.floor(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}
