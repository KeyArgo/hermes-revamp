// skins.js — My skins (Classic + skins you picked) and the Winamp Skin Museum.
// Clicking a museum skin downloads it once (the server caches it), applies it and saves the choice.
import { toast } from "./modal.js";

const BASE_SHOT = "https://r2.webampskins.org/screenshots/5e4f10275dcb1fb211d4a8b4f1bda236.png";
const PAGE = 30;

export async function render(root, api) {
  root.innerHTML = "";
  let activeId = "classic";
  try { const st = await api("/api/state"); activeId = st.skin || "classic"; } catch { /* offline */ }

  // ---- My skins
  const mineHead = heading("My skins");
  const mine = grid();
  root.append(mineHead, mine);

  async function loadMine() {
    let skins = [];
    try { skins = await api("/api/skins"); } catch (e) { mine.replaceChildren(empty(`Failed to load skins: ${e.message || e}`)); return; }
    // Classic falls back here when a saved skin is gone (e.g. an old built-in one).
    if (!skins.some((s) => s.id === activeId)) activeId = "classic";
    mine.replaceChildren(...skins.map((s) => card({
      name: s.id === "classic" ? "Classic (original Winamp)" : s.name,
      shot: s.id === "classic" ? BASE_SHOT : s.screenshot,
      active: s.id === activeId,
      onApply: () => apply(s.id, s.name),
      onRemove: s.source === "museum" && s.id !== activeId ? async () => {
        try { await api(`/api/skins/museum?md5=${s.id.slice(7)}`, "DELETE"); loadMine(); museumRefresh(); }
        catch (e) { toast(`Remove failed: ${e.message}`, { kind: "error" }); }
      } : null,
    })));
  }

  async function apply(id, name) {
    try {
      // The control page has no player of its own: ask the live one (same command agents use).
      if (window.revampPlayerApi?.setSkin) window.revampPlayerApi.setSkin(id);
      else await api("/api/command", "POST", { action: "skin", id });
      activeId = id;
      toast(`Skin: ${name}`, { kind: "ok" });
      loadMine();
    } catch (e) { toast(`Skin failed: ${e.message}`, { kind: "error" }); }
  }

  // ---- Winamp Skin Museum
  const museumHead = heading("Winamp Skin Museum");
  const credit = document.createElement("p");
  credit.className = "muted";
  credit.style.margin = "0 0 10px";
  credit.innerHTML = `Over 100,000 classic skins, made by their original authors and archived at
    <a href="https://skins.webamp.org" target="_blank" rel="noopener">skins.webamp.org</a>.
    Click one to download and apply it; it stays in My skins.`;
  const search = document.createElement("div");
  search.className = "searchbox";
  search.style.marginBottom = "10px";
  search.innerHTML = `<span class="icon" aria-hidden="true">⌕</span><input type="search" placeholder="Search the museum (zelda, green, metal…)" aria-label="Search the skin museum"/>`;
  const input = search.querySelector("input");
  const museum = grid();
  const more = document.createElement("button");
  more.className = "btn";
  more.style.margin = "12px auto";
  more.style.display = "none";
  more.textContent = "Load more";
  root.append(museumHead, credit, search, museum, more);

  let query = "";
  let next = 0;
  let seq = 0;
  async function loadMuseum(reset) {
    const mySeq = reset ? ++seq : seq;
    if (reset) { next = 0; museum.replaceChildren(empty("Loading…")); }
    more.disabled = true;
    try {
      const out = await api(`/api/skins/museum?${new URLSearchParams({ q: query, offset: String(next), limit: String(PAGE) })}`);
      if (mySeq !== seq) return;
      if (reset) museum.replaceChildren();
      for (const s of out.skins || []) museum.appendChild(museumCard(s));
      if (reset && !(out.skins || []).length) museum.replaceChildren(empty("No skins found."));
      next = out.next ?? null;
      more.style.display = next == null ? "none" : "";
    } catch (e) {
      if (mySeq === seq && reset) museum.replaceChildren(empty(`The museum is unreachable right now (${e.message || e}). Your skins still work.`));
    } finally { more.disabled = false; }
  }
  function museumRefresh() { loadMuseum(true); }

  function museumCard(s) {
    const c = card({
      name: s.name, shot: s.screenshot, active: s.id === activeId, link: s.museum_url,
      badge: s.installed ? "saved" : "",
      onApply: async () => {
        c.classList.add("busy");
        try {
          const entry = await api("/api/skins/museum", "POST", { md5: s.md5, name: s.name });
          apply(entry.id, entry.name);
          c.querySelector(".s-badge")?.replaceChildren("saved");
        } catch (e) { toast(`Could not get ${s.name}: ${e.message}`, { kind: "error" }); }
        finally { c.classList.remove("busy"); }
      },
    });
    return c;
  }

  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { query = input.value.trim(); loadMuseum(true); }, 400);
  });
  more.addEventListener("click", () => loadMuseum(false));

  await loadMine();
  loadMuseum(true);
}

function card({ name, shot, active, onApply, onRemove, link, badge }) {
  const c = document.createElement("div");
  c.className = "skin-card" + (active ? " active" : "");
  c.tabIndex = 0;
  c.setAttribute("role", "button");
  c.setAttribute("aria-label", `Apply skin ${name}`);
  const img = document.createElement("img");
  img.alt = "";
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  if (shot) img.src = shot;
  img.addEventListener("error", () => img.replaceWith(noPreview()), { once: true });
  if (!shot) queueMicrotask(() => img.dispatchEvent(new Event("error")));
  const row = document.createElement("div");
  row.className = "s-name";
  const nm = document.createElement("span");
  nm.textContent = name + (active ? " ✓" : "");
  row.appendChild(nm);
  if (badge) { const b = document.createElement("span"); b.className = "s-badge"; b.textContent = badge; row.appendChild(b); }
  if (link) {
    const a = document.createElement("a");
    a.href = link; a.target = "_blank"; a.rel = "noopener"; a.textContent = "↗";
    a.title = "Open in the Skin Museum"; a.setAttribute("aria-label", `${name} in the Skin Museum`);
    a.addEventListener("click", (e) => e.stopPropagation());
    // Keyboard Enter on the link must not also run the card's apply handler.
    a.addEventListener("keydown", (e) => { if (e.key === "Enter") e.stopPropagation(); });
    row.appendChild(a);
  }
  if (onRemove) {
    const x = document.createElement("button");
    x.className = "btn btn-ghost sm"; x.textContent = "✕";
    x.title = "Remove from My skins"; x.setAttribute("aria-label", `Remove ${name}`);
    x.addEventListener("click", (e) => { e.stopPropagation(); onRemove(); });
    row.appendChild(x);
  }
  c.append(img, row);
  c.addEventListener("click", () => { if (!active) onApply(); });
  c.addEventListener("keydown", (e) => { if (e.key === "Enter" && !active) onApply(); });
  return c;
}

function heading(text) {
  const h = document.createElement("h3");
  h.className = "skin-head";
  h.textContent = text;
  return h;
}
function grid() { const g = document.createElement("div"); g.className = "skin-grid"; return g; }
function noPreview(text = "no preview") {
  const d = document.createElement("div");
  d.style.cssText = "aspect-ratio:275/116;display:flex;align-items:center;justify-content:center;background:var(--bg-2);color:var(--txt-dim);font-size:12px";
  d.textContent = text;
  return d;
}
function empty(text) { const d = document.createElement("div"); d.className = "empty"; d.textContent = text; return d; }
