// npextras.js — now-playing extras on the full page: book controls (−10 / +30, speed,
// chapters) and synced lyrics. Fed by the player's onNow hook; plain DOM, no Webamp internals.

const RATES = [0.75, 1, 1.25, 1.5, 1.75, 2, 2.5];
const clock = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
  return `${h ? `${h}:${String(m).padStart(2, "0")}` : m}:${String(s % 60).padStart(2, "0")}`;
};

export function mount(api, viewApi) {
  const card = document.getElementById("np-card");
  if (!card) return;

  // ---- book row
  const row = document.createElement("div");
  row.className = "np-book";
  row.hidden = true;
  row.innerHTML = `
    <button type="button" class="btn sm" data-d="-10" title="Back 10 seconds">−10</button>
    <button type="button" class="btn sm" data-d="30" title="Forward 30 seconds">+30</button>
    <select class="np-rate" aria-label="Playback speed" title="Playback speed">
      ${RATES.map((r) => `<option value="${r}">${r}×</option>`).join("")}
    </select>
    <select class="np-chapter" aria-label="Chapter" title="Chapter" hidden></select>`;
  card.appendChild(row);
  const rateSel = row.querySelector(".np-rate");
  const chapSel = row.querySelector(".np-chapter");
  row.querySelectorAll("[data-d]").forEach((b) => b.addEventListener("click", () => api.seekBy(Number(b.dataset.d))));

  const savedRate = () => api.bookRate(current.item); // this book's speed (else the last one used)
  rateSel.addEventListener("change", () => {
    const r = api.setRate(Number(rateSel.value));
    api.saveBookRate(current.item, r);
  });

  // ---- lyrics
  const lyrBtn = document.createElement("button");
  lyrBtn.type = "button";
  lyrBtn.className = "btn sm np-lyrics-btn";
  lyrBtn.textContent = "♪ Lyrics";
  lyrBtn.setAttribute("aria-pressed", "false");
  lyrBtn.hidden = true;
  card.querySelector(".np-extras")?.appendChild(lyrBtn);
  const pane = document.createElement("div");
  pane.className = "lyrics-pane glass";
  pane.setAttribute("aria-live", "polite");
  pane.hidden = true;
  (document.querySelector(".stage-wrap") || document.body).appendChild(pane);
  lyrBtn.addEventListener("click", () => {
    pane.hidden = !pane.hidden;
    lyrBtn.setAttribute("aria-pressed", String(!pane.hidden));
    if (!pane.hidden) loadLyrics(current.tid);
  });

  // ---- visualizer (MilkDrop): always visible; pressed state follows the player
  const vizBtn = document.createElement("button");
  vizBtn.type = "button";
  vizBtn.className = "btn sm np-visualizer";
  vizBtn.textContent = "🌀 Visualizer";
  vizBtn.setAttribute("aria-pressed", "false");
  card.querySelector(".np-extras")?.appendChild(vizBtn);
  const paintViz = () => vizBtn.setAttribute("aria-pressed", String(!!api.visualizerOpen?.()));
  vizBtn.addEventListener("click", () => { api.toggleVisualizer?.(); paintViz(); });

  // ---- state per item
  let current = { key: null, tid: null, book: false, item: null };
  let chapters = [];
  let lyrics = null; // {synced, plain, found, instrumental}
  let lyricKey = null;
  let lastLine = -1;

  async function loadChapters(tid) {
    chapters = [];
    chapSel.hidden = true;
    if (!tid) return;
    try {
      const out = await viewApi(`/api/library/chapters?id=${encodeURIComponent(tid)}`);
      if (current.tid !== tid) return;
      chapters = Array.isArray(out?.chapters) ? out.chapters : [];
    } catch { chapters = []; } // older server or no chapters: just no picker
    chapSel.replaceChildren(...chapters.map((c, i) => {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = `${i + 1}. ${c.title} (${clock(c.start)})`;
      return o;
    }));
    chapSel.hidden = chapters.length < 2;
  }
  chapSel.addEventListener("change", () => {
    const c = chapters[Number(chapSel.value)];
    if (c) api.seek(c.start + 0.05);
  });

  async function loadLyrics(tid) {
    if (!tid || lyricKey === tid) return render();
    lyricKey = tid;
    lyrics = null;
    pane.textContent = "Looking for lyrics…";
    try {
      const out = await viewApi(`/api/library/lyrics?id=${encodeURIComponent(tid)}`);
      if (lyricKey !== tid) return;
      lyrics = out;
    } catch { lyrics = { found: false }; }
    lastLine = -1;
    render();
  }
  function render() {
    if (pane.hidden) return;
    if (!lyrics) return;
    if (!lyrics.found) { pane.textContent = lyrics.instrumental ? "Instrumental." : "No lyrics found for this song."; return; }
    if (lyrics.instrumental) { pane.textContent = "Instrumental."; return; }
    const lines = lyrics.synced?.length ? lyrics.synced : String(lyrics.plain || "").split("\n").map((line) => ({ t: null, line }));
    pane.replaceChildren(...lines.map((l, i) => {
      const d = document.createElement("div");
      d.className = "lyr";
      d.dataset.i = String(i);
      d.textContent = l.line || "♪";
      if (l.t != null) { d.title = clock(l.t); d.addEventListener("click", () => api.seek(l.t)); }
      return d;
    }));
    lastLine = -1;
  }
  function follow(pos) {
    if (pane.hidden || !lyrics?.synced?.length) return;
    let i = -1;
    for (let k = 0; k < lyrics.synced.length && lyrics.synced[k].t <= pos + 0.15; k++) i = k;
    if (i === lastLine) return;
    pane.querySelector(".lyr.on")?.classList.remove("on");
    const el = pane.querySelector(`.lyr[data-i="${i}"]`);
    if (el) { el.classList.add("on"); el.scrollIntoView({ block: "center", behavior: "smooth" }); }
    lastLine = i;
  }

  // ---- the player tells us what's playing (on each now-playing update)
  api.onNow = (it, info) => {
    const tid = api.localTid(it);
    const book = it?.kind === "audiobook";
    const key = it?.id || null;
    if (key !== current.key) {
      current = { key, tid, book, item: it };
      row.hidden = !book;
      lyrBtn.hidden = !(tid && !book && it?.kind !== "stream");
      if (lyrBtn.hidden && !pane.hidden) { pane.hidden = true; lyrBtn.setAttribute("aria-pressed", "false"); }
      api.setRate(book ? savedRate() : 1); // books at your speed, music always at 1×
      rateSel.value = String(book ? savedRate() : 1);
      if (book) loadChapters(tid); else { chapters = []; chapSel.hidden = true; }
      if (!pane.hidden) loadLyrics(tid);
    }
    const pos = Number(info?.position) || 0;
    if (chapters.length > 1) {
      let i = 0;
      for (let k = 0; k < chapters.length; k++) if (chapters[k].start <= pos + 0.5) i = k;
      if (chapSel.value !== String(i)) chapSel.value = String(i);
    }
  };
  // Lyrics follow the music more finely than the 2 s now-playing tick; the visualizer
  // button keeps its pressed state true to the player (its ✕, commands, messages).
  setInterval(() => {
    paintViz();
    if (pane.hidden) return;
    const m = api.webamp?.store?.getState?.()?.media;
    if (m) follow(Number(m.timeElapsed) || 0);
  }, 250);
}
