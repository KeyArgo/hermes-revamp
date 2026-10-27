// folders.js — the Music or Books folders, inline in the library views.
// A folder is Music or Books, never both: the server refuses that, and so do we.
import { toast } from "./modal.js";

const LABEL = { music: "Music", books: "Books" };

/** Mount the folder manager for `kind` ("music" | "books") into `root`. onChange runs after a save. */
export async function mountFolders(root, api, kind, onChange, alive = () => true) {
  let all = [];
  let browsing = null; // null = the list; otherwise the folder being browsed ("" = home)
  let busy = false;

  async function load() {
    try {
      const res = await api("/api/library/folders");
      all = (res.folders || []).map((f) => ({ ...f, kind: f.kind === "books" ? "books" : "music" }));
    } catch (e) { if (alive()) root.replaceChildren(note(`Couldn't load folders: ${e.message}`)); return false; }
    return true;
  }

  async function save(next) {
    if (busy) return;
    busy = true;
    try {
      await api("/api/library/folders", "PUT", { folders: next.map((f) => ({ path: f.path, kind: f.kind })) });
      await load();
      browsing = null;
      onChange?.();
    } catch (e) { toast(`Couldn't save folders: ${e.message}`, { kind: "error" }); }
    finally { busy = false; draw(); }
  }

  function draw() {
    if (!alive()) return; // closed or reopened meanwhile
    if (browsing !== null) return drawBrowse();
    const mine = all.filter((f) => f.kind === kind);
    const box = document.createElement("div");
    box.className = "folders glass";
    const head = document.createElement("div");
    head.className = "folders-head";
    head.textContent = `${LABEL[kind]} folders`;
    box.appendChild(head);
    if (!mine.length) box.appendChild(note(kind === "books"
      ? "No Books folders yet. Add the folder that holds your audiobooks."
      : "No Music folders yet. Add the folder that holds your music."));
    for (const f of mine) {
      const row = document.createElement("div");
      row.className = "folder-row";
      const txt = document.createElement("span");
      txt.className = "folder-path";
      txt.textContent = f.exists ? `${f.path}  ·  ${f.tracks} tracks` : `${f.path}  ·  folder not found`;
      const x = button("✕", "btn btn-ghost sm", () => save(all.filter((g) => g.path !== f.path)));
      x.title = "Stop using this folder";
      x.setAttribute("aria-label", `Remove ${f.path}`);
      row.append(txt, x);
      box.appendChild(row);
    }
    box.appendChild(button(`＋ Add a ${LABEL[kind]} folder…`, "btn sm", () => { browsing = ""; draw(); }));
    root.replaceChildren(box);
  }

  async function drawBrowse() {
    const box = document.createElement("div");
    box.className = "folders glass";
    const head = document.createElement("div");
    head.className = "folders-head";
    head.append(button("‹ Back", "btn btn-ghost sm", () => { browsing = null; draw(); }), ` Add a ${LABEL[kind]} folder`);
    const form = document.createElement("form");
    form.className = "btn-row folder-goto";
    const input = document.createElement("input");
    input.type = "text"; input.spellcheck = false;
    input.placeholder = "Type a folder path, e.g. /mnt/nas/music";
    input.setAttribute("aria-label", "Folder path");
    form.append(input, button("Go", "btn sm", null, "submit"));
    form.addEventListener("submit", (e) => { e.preventDefault(); const v = input.value.trim(); if (v) { browsing = v; draw(); } });
    const listEl = document.createElement("div");
    listEl.className = "folder-list";
    listEl.appendChild(note("Loading…"));
    const foot = document.createElement("div");
    foot.className = "btn-row";
    box.append(head, form, listEl, foot);
    root.replaceChildren(box);

    let here;
    try { here = await api(`/api/fs/dirs${browsing ? `?${new URLSearchParams({ path: browsing })}` : ""}`); }
    catch (e) { listEl.replaceChildren(note(`Can't open that folder: ${e.message}`)); return; }
    if (!box.isConnected || !alive()) return;
    input.value = here.path || "";
    const rows = [];
    if (here.parent) rows.push(button("‥  up one level", "folder-dir", () => { browsing = here.parent; draw(); }));
    for (const d of here.dirs || []) rows.push(button(`📁  ${d.name}`, "folder-dir", () => { browsing = d.path; draw(); }));
    if (!rows.length) rows.push(note("No subfolders."));
    listEl.replaceChildren(...rows);

    const taken = all.find((f) => f.path === here.path);
    if (taken) foot.appendChild(note(taken.kind === kind
      ? `Already a ${LABEL[kind]} folder.`
      : `Already a ${LABEL[taken.kind]} folder. A folder can't be both; remove it there first.`));
    const add = button(`Use this folder for ${LABEL[kind]}`, "btn sm btn-primary", () => save([...all, { path: here.path, kind }]));
    add.disabled = Boolean(taken) || !here.path;
    foot.append(Object.assign(document.createElement("span"), { style: "flex:1" }), add);
  }

  if (await load()) draw();
}

function button(text, cls, onClick, type = "button") {
  const b = document.createElement("button");
  b.type = type; b.className = cls; b.textContent = text;
  if (onClick) b.addEventListener("click", onClick);
  return b;
}
function note(text) { const d = document.createElement("div"); d.className = "muted folder-note"; d.textContent = text; return d; }
