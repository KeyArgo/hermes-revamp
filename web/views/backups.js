// backups.js — the Settings "Your data & backups" panel: snapshot now, restore from the
// rolling backups, export a zip, import one, plus the empty-start "Welcome back" offer.
import { confirmDialog, openModal, toast } from "./modal.js";

const OFFERED_KEY = "revamp.restoreOffered";

/** One row per backup: when it was taken, how many files, restore button. */
function backupRow(manifest, onRestore) {
  const li = document.createElement("li");
  const when = new Date(manifest.created * 1000).toLocaleString();
  const fileKeys = Object.keys(manifest.files || {});
  const playlists = fileKeys.filter((k) => k.startsWith("playlists/")).length;
  const info = document.createElement("span");
  info.textContent = `${when} · ${fileKeys.length} files` + (playlists ? ` · ${playlists} playlist${playlists === 1 ? "" : "s"}` : "");
  const btn = document.createElement("button");
  btn.className = "btn sm bk-restore";
  btn.textContent = "Restore";
  btn.addEventListener("click", () => onRestore(manifest));
  li.append(info, btn);
  return li;
}

/** POST /api/backups/restore, toast, then reload so every view re-reads its data. */
async function doRestore(api, id) {
  window.revampLocalRestore = true; // this tab reloads itself below; the server's "restored" event skips it
  try {
    await api("/api/backups/restore", "POST", { id });
    toast("Restored. Reloading…", { kind: "ok" });
    setTimeout(() => location.reload(), 800);
    return true;
  } catch (e) {
    window.revampLocalRestore = false;
    toast(`Restore failed: ${e.message}`, { kind: "error" });
    return false;
  }
}

/** The confirm-guarded restore flow used by the list's Restore buttons. */
function restoreWithConfirm(api, manifest) {
  const when = new Date(manifest.created * 1000).toLocaleString();
  confirmDialog(
    `Restore the backup from ${when}? Your current data is backed up first, so you can undo this.`,
    { title: "Restore backup", okLabel: "Restore" },
  ).then(async (ok) => {
    if (!ok) return;
    // The server snapshots current data before restoring, so "undo" exists either way.
    await doRestore(api, manifest.id);
  });
}

/** Fetch the zip with the token header and save it via a temp <a download>. */
async function exportZip(api) {
  try {
    // The token travels in a header: a token in a URL can end up in logs and history.
    const res = await fetch("/api/backups/export", { headers: { "X-Revamp-Token": api.token } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "revamp-backup.zip";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (e) {
    toast(`Export failed: ${e.message}`, { kind: "error" });
  }
}

/** Base64 in chunks so a 50 MB file never blows the call stack (String.fromCharCode(...)). */
function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("could not read file"));
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result);
      let bin = "";
      const CH = 0x8000;
      for (let i = 0; i < bytes.length; i += CH) {
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
      }
      resolve(btoa(bin));
    };
    reader.readAsArrayBuffer(blob);
  });
}

/** Read the chosen zip, base64 it, POST it, then reload (other open tabs reload on the server's event). */
async function importZip(api, file, fileInput) {
  if (!file) return;
  window.revampLocalRestore = true;
  try {
    const zip_b64 = await blobToBase64(file);
    await api("/api/backups/import", "POST", { zip_b64 });
    toast("Imported. Reloading…", { kind: "ok" });
    setTimeout(() => location.reload(), 800);
  } catch (e) {
    window.revampLocalRestore = false;
    toast(`Import failed: ${e.message}`, { kind: "error" });
  }
  if (fileInput) fileInput.value = "";
}

/** Build the Settings section. Same viewApi convention as every view. */
export async function mountBackups(root, api, alive = () => true) {
  const section = document.createElement("section");
  section.id = "backups";
  section.className = "glass";
  section.style.cssText = "padding:12px 14px;margin-bottom:14px";

  const h = document.createElement("h3");
  h.textContent = "Your data & backups";
  h.style.cssText = "margin:0 0 4px;font-size:14px";
  section.appendChild(h);

  const hint = document.createElement("div");
  hint.className = "hint";
  hint.style.marginBottom = "8px";
  hint.textContent = "REVamp keeps rolling backups of your likes, playlists and settings — one every 10 minutes while you use it.";
  section.appendChild(hint);

  const bar = document.createElement("div");
  bar.className = "btn-row";
  bar.style.marginBottom = "8px";
  bar.innerHTML = `
    <button class="btn sm btn-primary" id="bk-now">Back up now</button>
    <button class="btn sm" id="bk-export">Export…</button>
    <button class="btn sm" id="bk-import">Import…</button>
    <input type="file" id="bk-file" accept=".zip" hidden />`;
  section.appendChild(bar);

  const list = document.createElement("ul");
  list.id = "bk-list";
  list.className = "list";
  list.style.cssText = "margin:0;padding:0;list-style:none";
  section.appendChild(list);

  const PAGE = 20;
  let shown = PAGE; // rendered row count, raised by Load more (paging until all shown)

  async function refresh() {
    let data;
    try {
      data = await api("/api/backups");
    } catch (e) {
      if (alive()) list.replaceChildren();
      toast(`Couldn't load backups: ${e.message}`, { kind: "error" });
      return;
    }
    if (!alive()) return;
    const backups = Array.isArray(data?.backups) ? data.backups : [];
    shown = Math.min(shown, Math.max(PAGE, backups.length)) || PAGE;
    list.replaceChildren();
    if (!backups.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No backups yet.";
      list.appendChild(li);
      return;
    }
    for (const b of backups.slice(0, shown)) list.appendChild(backupRow(b, (m) => restoreWithConfirm(api, m)));
    // Older snapshots stay reachable: reveal the next page on demand.
    if (backups.length > shown) {
      const more = document.createElement("li");
      const btn = document.createElement("button");
      btn.id = "bk-more";
      btn.className = "btn btn-ghost";
      btn.type = "button";
      btn.textContent = `Load more (${backups.length - shown} older)`;
      btn.addEventListener("click", () => { shown += PAGE; paint(backups); });
      more.appendChild(btn);
      list.appendChild(more);
    }
  }

  function paint(backups) {
    list.replaceChildren();
    for (const b of backups.slice(0, shown)) list.appendChild(backupRow(b, (m) => restoreWithConfirm(api, m)));
    if (backups.length > shown) {
      const more = document.createElement("li");
      const btn = document.createElement("button");
      btn.id = "bk-more";
      btn.className = "btn btn-ghost";
      btn.type = "button";
      btn.textContent = `Load more (${backups.length - shown} older)`;
      btn.addEventListener("click", () => { shown += PAGE; paint(backups); });
      more.appendChild(btn);
      list.appendChild(more);
    }
  }

  bar.querySelector("#bk-now").addEventListener("click", async () => {
    try {
      const r = await api("/api/backups/snapshot", "POST", {});
      toast(r?.backup ? "Backed up" : "Nothing changed since the last backup", { kind: "ok" });
    } catch (e) {
      toast(`Backup failed: ${e.message}`, { kind: "error" });
    }
    refresh();
  });
  bar.querySelector("#bk-export").addEventListener("click", () => exportZip(api));
  bar.querySelector("#bk-import").addEventListener("click", () => bar.querySelector("#bk-file").click());
  bar.querySelector("#bk-file").addEventListener("change", (e) => importZip(api, e.target.files[0], e.target));

  refresh();
  return section;
}

/** On boot: if REVamp is empty but backups exist, offer a one-click restore (once per session). */
export async function maybeOfferRestore(api) {
  let data;
  try {
    data = await api("/api/backups");
  } catch {
    return;
  }
  // The newest backup that holds real data: a fresh install's own (empty) first snapshot is skipped.
  const best = (data?.backups || []).find((b) => !b.empty);
  if (!data?.data_empty || !best) return;
  if (sessionStorage.getItem(OFFERED_KEY)) return;
  sessionStorage.setItem(OFFERED_KEY, "1");
  const when = new Date(best.created * 1000).toLocaleString();
  const body = document.createElement("div");
  const p = document.createElement("p");
  p.textContent = `REVamp is empty, but there is a backup from ${when}. Restore it?`;
  body.appendChild(p);
  await openModal({
    title: "Welcome back",
    body,
    actions: [
      { label: "Not now", value: false },
      { label: "Restore", value: true, primary: true },
    ],
  }).then(async (ok) => {
    if (ok) await doRestore(api, best.id);
  });
}
