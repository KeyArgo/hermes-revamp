// radio.js — Hermes Radio (the desktop lineup) / Popular / Browse / Search / Favorites.
// Every directory list pages in with an IntersectionObserver sentinel and keeps
// going until Radio Browser runs out of stations, like the Music view.
import { textPrompt, toast } from "./modal.js";

const PAGE = 50;
// No in-app composer: Hermes Desktop doesn't take a prompt from this frame.
// The button copies this so the user can paste it where Hermes is listening.
const ASK_HERMES = "Play something on Hermes Radio that fits what I'm doing";

const stationItem = (st) => ({
  kind: "stream", title: st.name, artist: (Array.isArray(st.tags) ? st.tags.slice(0, 2).join(", ") : "") || st.country || "",
  url: st.url, duration: null, station_id: st.id ?? null,
});

const flagFor = (code) => {
  const c = String(code || "").toUpperCase();
  if (!/^[A-Z]{2}$/.test(c)) return "";
  return [...c].map((ch) => String.fromCodePoint(127397 + ch.charCodeAt(0))).join("");
};

const SORTS = [["top", "Top"], ["votes", "Votes"], ["trending", "Trending"]];

export async function render(root, api) {
  root.innerHTML = "";

  let tab = "hermes";
  let favIds = new Set();
  let stationMenu = null;
  let stationMenuCleanup = null;

  // ---- toolbar
  const bar = document.createElement("div");
  bar.className = "btn-row radio-toolbar";
  bar.style.marginBottom = "10px";
  const tabs = document.createElement("div");
  tabs.className = "tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Radio");
  tabs.innerHTML = `
    <button type="button" data-t="hermes" class="active" role="tab" aria-selected="true">Hermes Radio</button>
    <button type="button" data-t="popular" role="tab" aria-selected="false">Popular</button>
    <button type="button" data-t="browse" role="tab" aria-selected="false">Browse</button>
    <button type="button" data-t="search" role="tab" aria-selected="false">Search</button>
    <button type="button" data-t="favorites" role="tab" aria-selected="false">Favorites</button>`;
  const addBtn = document.createElement("button");
  addBtn.className = "btn sm";
  addBtn.style.marginLeft = "auto";
  addBtn.textContent = "＋ Add stream URL";
  const sortWrap = document.createElement("div");
  sortWrap.className = "tabs radio-sorts";
  sortWrap.style.marginLeft = "8px";
  bar.append(tabs, sortWrap, addBtn);
  root.appendChild(bar);

  // status line like the Music view's "120 / 1500"
  const statusRow = document.createElement("div");
  statusRow.className = "btn-row radio-status";
  statusRow.style.margin = "0 0 8px";
  const filterNote = document.createElement("span");
  filterNote.className = "muted";
  const statusEl = document.createElement("span");
  statusEl.className = "muted";
  statusEl.style.marginLeft = "auto";
  statusRow.append(filterNote, statusEl);
  root.appendChild(statusRow);

  const searchWrap = document.createElement("div");
  searchWrap.className = "searchbox";
  searchWrap.style.cssText = "margin-bottom:10px;display:none";
  searchWrap.innerHTML = `<span class="icon" aria-hidden="true">⌕</span><input type="search" placeholder="Search station names… (use #tag for a genre)" aria-label="Search radio"/>`;
  root.appendChild(searchWrap);
  const searchInput = searchWrap.querySelector("input");

  const noticeEl = document.createElement("div");
  noticeEl.className = "radio-notice";
  noticeEl.hidden = true;
  root.appendChild(noticeEl);

  const listEl = document.createElement("div");
  listEl.className = "radio-list";
  root.appendChild(listEl);

  // ---- endless list machinery (one live generator at a time; a newer load wins)
  let listGen = 0;
  let listObserver = null;
  let stopLive = () => {};

  function newList() {
    listGen += 1;
    listObserver?.disconnect();
    stopLive();
    stopLive = () => {};
    listEl.innerHTML = "";
    return listGen;
  }

  function attachSentinel(loadNext, done) {
    const sentinel = document.createElement("div");
    sentinel.className = "radio-sentinel";
    sentinel.innerHTML = done ? "" : `<span class="spinner"></span>`;
    listEl.appendChild(sentinel);
    if (done) return;
    listObserver = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) loadNext();
    }, { rootMargin: "600px 0px" });
    listObserver.observe(sentinel);
  }

  // ---- station rows -------------------------------------------------------
  function paintFav(btn, st) {
    const on = favIds.has(st.id);
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "♥" : "♡";
    btn.title = on ? "Remove from favorites" : "Add to favorites";
    btn.setAttribute("aria-label", `${on ? "Unfavorite" : "Favorite"}: ${st.name}`);
  }

  function stationRow(st, getList) {
    const row = document.createElement("div");
    row.className = "radio-row card-row";
    row.tabIndex = 0;

    const art = document.createElement("div");
    art.className = "radio-art";
    if (st.favicon) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = "";
      img.addEventListener("error", () => { img.remove(); art.classList.add("none"); });
      img.src = st.favicon;
      art.appendChild(img);
    } else {
      art.classList.add("none");
    }

    const grow = document.createElement("div");
    grow.className = "grow";
    const nm = document.createElement("div");
    nm.className = "name";
    const flag = flagFor(st.countrycode || st.country);
    if (flag) {
      const f = document.createElement("span");
      f.className = "radio-flag";
      f.textContent = flag;
      f.setAttribute("aria-hidden", "true");
      nm.appendChild(f);
    }
    nm.append(document.createTextNode(st.name || "Unnamed"));
    const sub = document.createElement("div");
    sub.className = "sub radio-sub";
    for (const t of (Array.isArray(st.tags) ? st.tags.slice(0, 4) : [])) {
      const chip = document.createElement("span");
      chip.className = "radio-chip";
      chip.textContent = t;
      sub.appendChild(chip);
    }
    const bits = [];
    if (st.bitrate) bits.push(`${st.bitrate} kbps`);
    if (st.codec) bits.push(st.codec);
    if (st.country && st.countrycode !== st.country) bits.push(st.country);
    if (bits.length) {
      const meta = document.createElement("span");
      meta.className = "radio-meta";
      meta.textContent = bits.join(" · ");
      sub.appendChild(meta);
    }
    grow.append(nm, sub);

    const btns = document.createElement("div");
    btns.className = "btn-row";
    const play = document.createElement("button");
    play.className = "btn sm btn-primary";
    play.textContent = "▶";
    play.setAttribute("aria-label", `Play ${st.name}`);
    play.addEventListener("click", (e) => {
      e.stopPropagation();
      const list = getList();
      window.revampPlayerApi.playItemsAt(Math.max(0, list.indexOf(st)), list.map(stationItem));
    });
    const heart = document.createElement("button");
    heart.type = "button";
    heart.className = "like-btn radio-heart";
    paintFav(heart, st);
    heart.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        if (favIds.has(st.id)) {
          await api(`/api/radio/favorites?id=${encodeURIComponent(st.id)}`, "DELETE");
          favIds.delete(st.id);
        } else {
          await api("/api/radio/favorites", "POST", st);
          favIds.add(st.id);
        }
        paintFav(heart, st);
      } catch (err) { toast(err.message, { kind: "error" }); }
    });
    btns.append(play, heart);
    row.append(art, grow, btns);

    row.addEventListener("dblclick", () => play.click());
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target === row) play.click();
      if ((e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) && e.target === row) {
        e.preventDefault();
        const rect = row.getBoundingClientRect();
        openStationMenu(st, getList, { x: rect.left, y: rect.bottom });
      }
    });
    row.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      openStationMenu(st, getList, { x: e.clientX, y: e.clientY });
    });
    return row;
  }

  function closeStationMenu(returnFocus = false) {
    if (!stationMenu) return;
    const { element, row } = stationMenu;
    stationMenu = null;
    stationMenuCleanup?.();
    stationMenuCleanup = null;
    element.remove();
    if (returnFocus) row?.focus();
  }

  function openStationMenu(st, getList, point) {
    closeStationMenu();
    const menu = document.createElement("div");
    menu.className = "track-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "Station actions");
    const header = document.createElement("div");
    header.className = "track-menu-header";
    header.textContent = st.name || "Unnamed";
    menu.appendChild(header);
    const addItem = (label, action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "menuitem");
      button.textContent = label;
      button.addEventListener("click", () => { closeStationMenu(); action(); });
      menu.appendChild(button);
    };
    addItem("▶ Play", () => {
      const list = getList();
      window.revampPlayerApi.playItemsAt(Math.max(0, list.indexOf(st)), list.map(stationItem));
    });
    addItem("＋ Add to queue", () => window.revampPlayerApi.loadTracks([stationItem(st)], { mode: "append" }));
    addItem(favIds.has(st.id) ? "♥ Unfavorite" : "♡ Favorite", () => {
      if (favIds.has(st.id)) {
        api(`/api/radio/favorites?id=${encodeURIComponent(st.id)}`, "DELETE")
          .then(() => { favIds.delete(st.id); repaintHearts(); })
          .catch((e) => toast(e.message, { kind: "error" }));
      } else {
        api("/api/radio/favorites", "POST", st)
          .then(() => { favIds.add(st.id); repaintHearts(); })
          .catch((e) => toast(e.message, { kind: "error" }));
      }
    });
    if (st.homepage) {
      addItem("🌐 Open website", () => window.open(st.homepage, "_blank", "noopener"));
    }
    root.appendChild(menu);
    const rect = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(point.x, window.innerWidth - rect.width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(point.y, window.innerHeight - rect.height - 8))}px`;
    const rows = [...listEl.querySelectorAll(".radio-row")];
    stationMenu = { element: menu, row: rows.find((r) => r.querySelector(".name")?.textContent.includes(st.name)) };
    const onKeydown = (e) => {
      const buttons = [...menu.querySelectorAll('[role="menuitem"]')];
      const current = buttons.indexOf(document.activeElement);
      if (e.key === "Escape") { e.preventDefault(); closeStationMenu(true); }
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        buttons[(current + (e.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length].focus();
      } else if (e.key === "Enter" && current >= 0) { e.preventDefault(); buttons[current].click(); }
    };
    const onOutside = (e) => { if (!menu.contains(e.target)) closeStationMenu(); };
    menu.querySelector('[role="menuitem"]')?.focus();
    document.addEventListener("keydown", onKeydown);
    document.addEventListener("click", onOutside);
    stationMenuCleanup = () => {
      document.removeEventListener("keydown", onKeydown);
      document.removeEventListener("click", onOutside);
    };
  }

  function repaintHearts() {
    listEl.querySelectorAll(".radio-row").forEach((row) => {
      const btn = row.querySelector(".radio-heart");
      if (!btn) return;
      const st = row.__station;
      if (st) paintFav(btn, st);
    });
  }

  // ---- endless loaders ----------------------------------------------------
  function setNotice(text) {
    noticeEl.hidden = !text;
    noticeEl.textContent = text || "";
  }

  function setFilterNote(node) {
    filterNote.replaceChildren();
    if (node) filterNote.appendChild(node);
  }

  function errEmpty(el, msg, icon = "📡") {
    const d = document.createElement("div");
    d.className = "empty";
    const b = document.createElement("span");
    b.className = "big";
    b.textContent = icon;
    const p = document.createElement("p");
    p.textContent = msg;
    d.append(b, p);
    el.appendChild(d);
  }

  // Endless list of stations from a page fetcher. The caller must have cleared
  // listEl (newList()) first — chrome (headers) added by the caller survives.
  // fetchPage(offset) must resolve to {stations, more}.
  function endless(fetchPage, { emptyMsg = "No stations found.", offline = null } = {}) {
    const gen = listGen;
    let items = [];
    let rawOffset = 0;
    let done = false;
    let loading = false;
    let total = null;
    let spinnerEl = null;
    setNotice(null);

    const getList = () => items;
    const hideSpinner = () => { spinnerEl?.remove(); spinnerEl = null; };
    function paint() {
      if (gen !== listGen) return;
      statusEl.textContent = total != null ? `${items.length} / ${total}` : `${items.length} loaded`;
    }
    async function loadNext() {
      if (loading || done || gen !== listGen) return;
      loading = true;
      if (!items.length && !spinnerEl) {
        spinnerEl = document.createElement("div");
        spinnerEl.style.cssText = "padding:16px";
        spinnerEl.innerHTML = `<span class="spinner"></span>`;
        listEl.appendChild(spinnerEl);
      }
      let page;
      try {
        page = await fetchPage(rawOffset, PAGE);
      } catch (e) {
        loading = false;
        if (gen !== listGen) return;
        hideSpinner();
        if (!items.length) {
          const shown = offline ? await offline() : false;
          if (!shown) errEmpty(listEl, `Radio Browser trouble: ${e.message || e}`);
          else paint();
        }
        done = true;
        return;
      }
      loading = false;
      if (gen !== listGen) return;
      hideSpinner();
      const found = page.stations || [];
      for (const st of found) {
        const row = stationRow(st, getList);
        row.__station = st;
        listEl.appendChild(row);
      }
      items.push(...found);
      rawOffset += PAGE;
      if (page.total != null) total = page.total;
      done = !page.more || found.length === 0;
      paint();
      if (!items.length && done) {
        const shown = offline ? await offline() : false;
        if (!shown) errEmpty(listEl, emptyMsg);
      }
      attachSentinel(loadNext, done);
    }
    loadNext();
    return getList;
  }

  // ---- Hermes Radio: the desktop lineup, big tiles, nothing plays by itself
  function currentStream() {
    const player = window.revampPlayerApi;
    if (!player?.webamp?.store?.getState) return { url: null, status: "STOPPED" };
    try {
      const st = player.webamp.store.getState();
      const status = st?.media?.status || "STOPPED";
      const pl = st?.playlist;
      const order = Array.isArray(pl?.trackOrder) ? pl.trackOrder : [];
      const idx = order.indexOf(pl?.currentTrack);
      const item = (player.items || [])[idx];
      const url = item?.kind === "stream" ? (item.playback?.stream || null) : null;
      return { url, status };
    } catch {
      return { url: null, status: "STOPPED" };
    }
  }

  function paintTransport(cur) {
    listEl.querySelectorAll(".hermes-station").forEach((el) => {
      const playing = cur.status === "PLAYING" && cur.url === el.dataset.url;
      el.classList.toggle("is-playing", playing);
      const btn = el.querySelector(".hermes-play");
      if (!btn) return;
      const name = el.dataset.name || "station";
      btn.textContent = playing ? "⏸" : "▶";
      btn.setAttribute("aria-pressed", playing ? "true" : "false");
      btn.setAttribute("aria-label", `${playing ? "Pause" : "Play"} ${name}`);
    });
  }

  function paintTitle(url, title) {
    listEl.querySelectorAll(".hermes-now").forEach((el) => {
      const on = !!(url && title && el.dataset.url === url);
      el.hidden = !on;
      const name = el.querySelector(".hermes-now-title");
      if (name) name.textContent = on ? title : "";
    });
  }

  function paintHero(lineup, gen) {
    const hero = document.createElement("section");
    hero.className = "hermes-hero";
    hero.setAttribute("aria-labelledby", "hermes-radio-heading");

    const head = document.createElement("div");
    head.className = "hermes-hero-head";
    const intro = document.createElement("div");
    intro.className = "hermes-hero-intro";
    const h = document.createElement("h2");
    h.id = "hermes-radio-heading";
    h.textContent = "Hermes Radio";
    const lead = document.createElement("p");
    lead.className = "hermes-hero-lead";
    lead.textContent = "The lineup from Hermes. Press play to start a station.";
    intro.append(h, lead);
    const ask = document.createElement("button");
    ask.type = "button";
    ask.className = "btn hermes-ask";
    ask.textContent = "Ask Hermes to pick";
    ask.addEventListener("click", async () => {
      const copied = await copyText(ASK_HERMES);
      toast(copied ? `Copied for Hermes: ${ASK_HERMES}` : ASK_HERMES, { kind: copied ? "ok" : "info", ttl: 6000 });
    });
    head.append(intro, ask);

    const grid = document.createElement("div");
    grid.className = "hermes-stations";
    grid.setAttribute("role", "list");
    lineup.forEach((st, index) => {
      const card = document.createElement("article");
      card.className = "hermes-station";
      card.setAttribute("role", "listitem");
      card.dataset.url = st.url;
      card.dataset.name = st.name;
      const copy = document.createElement("div");
      copy.className = "hermes-station-copy";
      const name = document.createElement("h3");
      name.className = "hermes-station-name";
      name.textContent = st.name;
      const provider = document.createElement("p");
      provider.className = "hermes-station-provider";
      provider.textContent = st.provider || "";
      const desc = document.createElement("p");
      desc.className = "hermes-station-desc";
      desc.textContent = st.description || "";
      const now = document.createElement("p");
      now.className = "hermes-now";
      now.dataset.url = st.url;
      now.setAttribute("aria-live", "polite");
      now.hidden = true;
      const nowK = document.createElement("span");
      nowK.className = "hermes-now-k";
      nowK.textContent = "Now playing";
      const nowT = document.createElement("span");
      nowT.className = "hermes-now-title";
      now.append(nowK, nowT);
      copy.append(name, provider, desc, now);
      const play = document.createElement("button");
      play.type = "button";
      play.className = "btn sm btn-primary hermes-play";
      play.textContent = "▶";
      play.setAttribute("aria-pressed", "false");
      play.setAttribute("aria-label", `Play ${st.name}`);
      play.addEventListener("click", () => {
        const player = window.revampPlayerApi;
        if (!player?.playItemsAt) { toast("The player isn't ready yet", { kind: "error" }); return; }
        const cur = currentStream();
        let done = Promise.resolve();
        if (cur.url === st.url && cur.status === "PLAYING") player.pause();
        else if (cur.url === st.url) player.play();
        else done = Promise.resolve(player.playItemsAt(index, lineup.map(hermesItem)));
        notePlaying(gen);
        done.then(() => { if (gen === listGen) notePlaying(gen); }).catch(() => {});
      });
      card.append(copy, play);
      grid.appendChild(card);
    });

    const soon = document.createElement("article");
    soon.className = "hermes-soon";
    soon.setAttribute("role", "listitem");
    soon.dataset.comingSoon = "true";
    const soonTitle = document.createElement("h3");
    soonTitle.className = "hermes-soon-title";
    soonTitle.textContent = "Hermes FM — coming soon";
    const soonBody = document.createElement("p");
    soonBody.textContent = "A station of our own, where Hermes DJs between your library and live radio.";
    soon.append(soonTitle, soonBody);
    grid.appendChild(soon);

    hero.append(head, grid);
    listEl.appendChild(hero);

    let metaUrl = "";
    let metaAt = 0;
    let timer = 0;
    async function fetchMeta(url) {
      let title = null;
      try {
        const np = await api("/api/radio/nowplaying?url=" + encodeURIComponent(url));
        title = np?.title || null;
      } catch { title = null; }
      if (gen !== listGen || metaUrl !== url) return;
      paintTitle(url, title);
    }
    function notePlaying(activeGen) {
      if (!root.isConnected) { stopLive(); return; }
      if (activeGen !== listGen) return;
      const cur = currentStream();
      paintTransport(cur);
      const url = cur.status === "PLAYING" && lineup.some((s) => s.url === cur.url) ? cur.url : "";
      if (!url) { metaUrl = ""; paintTitle(null, null); return; }
      const stale = Date.now() - metaAt > 15000;
      if (url !== metaUrl || stale) {
        metaUrl = url;
        metaAt = Date.now();
        fetchMeta(url);
      }
    }
    let unsub = null;
    timer = setInterval(() => notePlaying(gen), 15000);
    try {
      const store = window.revampPlayerApi?.webamp?.store;
      if (typeof store?.subscribe === "function") unsub = store.subscribe(() => notePlaying(gen));
    } catch { /* player without a store still paints on the interval */ }
    stopLive = () => {
      clearInterval(timer);
      const off = unsub;
      unsub = null;
      try { off?.(); } catch { /* already gone */ }
    };
    notePlaying(gen);
  }

  function hermesItem(st) {
    return {
      kind: "stream", title: st.name, artist: st.provider || "",
      url: st.url, duration: null, station_id: st.id ?? null,
    };
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch { /* denied, or this frame has no clipboard */ }
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "0";
      ta.style.top = "0";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return !!ok;
    } catch {
      return false;
    }
  }

  async function loadHermes() {
    sortWrap.replaceChildren();
    setFilterNote(null);
    setNotice(null);
    const gen = newList();
    statusEl.textContent = "";
    let stations = [];
    try {
      stations = await api("/api/radio/hermes");
    } catch (e) {
      if (gen !== listGen) return;
      errEmpty(listEl, e.message || "Hermes Radio is unavailable.");
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "btn sm";
      retry.textContent = "Retry";
      retry.addEventListener("click", () => loadHermes());
      listEl.appendChild(retry);
      return;
    }
    if (gen !== listGen) return;
    const lineup = (Array.isArray(stations) ? stations : []).filter((s) => s && s.url && s.name);
    paintHero(lineup, gen);
    statusEl.textContent = lineup.length ? `${lineup.length} stations` : "";
  }

  function loadPopular() {
    sortWrap.replaceChildren();
    setFilterNote(null);
    const gen = newList();
    void gen;
    endless(async (offset) => {
      const rows = await api(`/api/radio/popular?limit=${PAGE}&offset=${offset}`);
      const stations = Array.isArray(rows) ? rows : [];
      // the server quietly keeps the presets when Radio Browser is down: say so
      if (offset === 0 && stations.length && !stations.some((s) => s.source === "radiobrowser")) {
        setNotice("Radio Browser is unreachable — showing the built-in presets instead. They still play.");
      }
      return { stations, more: stations.length === PAGE };
    }, {
      emptyMsg: "No stations available.",
      offline: async () => {
        const rows = await api("/api/radio/presets").catch(() => null);
        if (!rows || !rows.length) return false;
        setNotice("Radio Browser is unreachable — showing the built-in presets instead. They still play.");
        rows.forEach((st) => {
          const row = stationRow(st, () => rows);
          row.__station = st;
          listEl.appendChild(row);
        });
        statusEl.textContent = `${rows.length} presets`;
        return true;
      },
    });
  }

  async function loadFavorites() {
    sortWrap.replaceChildren();
    setFilterNote(null);
    setNotice(null);
    const gen = newList();
    statusEl.textContent = "";
    listEl.innerHTML = `<div style="padding:16px"><span class="spinner"></span></div>`;
    let favs = [];
    try { favs = await api("/api/radio/favorites"); } catch (e) { if (gen === listGen) { listEl.innerHTML = ""; errEmpty(listEl, e.message, "☆"); } return; }
    if (gen !== listGen) return;
    favIds = new Set(favs.map((f) => f.id));
    listEl.innerHTML = "";
    if (!favs.length) return errEmpty(listEl, "No favorite stations yet. Play a preset, ★ anything in Popular or Search.", "☆");
    favs.forEach((st) => {
      const row = stationRow(st, () => favs);
      row.__station = st;
      listEl.appendChild(row);
    });
    statusEl.textContent = `${favs.length} favorites`;
  }

  // ---- Browse: genres / countries / languages chips -> endless filtered list
  const catCache = {};
  let catKind = "tags";
  let activeFilter = null; // {kind, value, name}
  let browseSort = "top";

  function sortButtons(onPick) {
    for (const [id, label] of SORTS) {
      const b = document.createElement("button");
      b.textContent = label;
      b.className = browseSort === id ? "active" : "";
      b.addEventListener("click", () => { browseSort = id; onPick(); });
      sortWrap.appendChild(b);
    }
  }

  async function loadBrowse() {
    sortWrap.replaceChildren();
    setNotice(null);
    if (activeFilter) return renderFilteredList();
    const gen = newList();
    setFilterNote(null);
    statusEl.textContent = "";
    listEl.innerHTML = `<div style="padding:16px"><span class="spinner"></span></div>`;
    let kinds;
    try {
      if (!catCache[catKind]) catCache[catKind] = await api(`/api/radio/categories?kind=${catKind}`);
      kinds = catCache[catKind];
    } catch (e) {
      if (gen !== listGen) return;
      listEl.innerHTML = "";
      setNotice(`Radio Browser is unreachable right now (${e.message || e}). Popular and Favorites still work from what we have cached.`);
      const subRow = document.createElement("div");
      subRow.className = "btn-row";
      subRow.style.cssText = "justify-content:center;margin-top:6px";
      const retry = document.createElement("button");
      retry.className = "btn sm";
      retry.textContent = "Retry";
      retry.addEventListener("click", loadBrowse);
      subRow.appendChild(retry);
      listEl.appendChild(subRow);
      return;
    }
    if (gen !== listGen) return;
    listEl.innerHTML = "";

    // category kind sub-tabs
    const kindsBar = document.createElement("div");
    kindsBar.className = "tabs radio-kinds";
    kindsBar.style.marginBottom = "8px";
    for (const [kind, label] of [["tags", "Genres"], ["countries", "Countries"], ["languages", "Languages"]]) {
      const b = document.createElement("button");
      b.textContent = label;
      if (kind === catKind) b.classList.add("active");
      b.addEventListener("click", () => { catKind = kind; loadBrowse(); });
      kindsBar.appendChild(b);
    }
    listEl.appendChild(kindsBar);

    if (!kinds.length) return errEmpty(listEl, "Nothing came back for this group.", "🗂");
    const grid = document.createElement("div");
    grid.className = "radio-chipgrid";
    kinds.forEach((cat) => {
      const chip = document.createElement("button");
      chip.className = "radio-catcard";
      chip.type = "button";
      const nm = document.createElement("span");
      nm.className = "cat-name";
      nm.textContent = cat.name;
      const ct = document.createElement("span");
      ct.className = "cat-count";
      ct.textContent = cat.count ? cat.count.toLocaleString() : "";
      chip.append(nm, ct);
      chip.addEventListener("click", () => {
        activeFilter = { kind: catKind, value: cat.value, name: cat.name };
        loadBrowse();
      });
      grid.appendChild(chip);
    });
    listEl.appendChild(grid);
    statusEl.textContent = `${kinds.length} ${catKind}`;
  }

  function renderFilteredList() {
    const gen = newList();
    listEl.innerHTML = "";
    // header: what's active, back and clear
    const head = document.createElement("div");
    head.className = "btn-row radio-filter-head";
    const label = document.createElement("span");
    label.textContent = `${catKind === "tags" ? "Genre" : catKind === "countries" ? "Country" : "Language"}: ${activeFilter.name}`;
    label.style.fontWeight = "600";
    const back = document.createElement("button");
    back.className = "btn sm btn-ghost";
    back.textContent = "← Categories";
    back.addEventListener("click", () => { activeFilter = null; loadBrowse(); });
    head.append(label, back);
    listEl.appendChild(head);
    setFilterNote(document.createTextNode(`${activeFilter.name} · `));
    sortButtons(renderFilteredList);

    const params = new URLSearchParams({ limit: String(PAGE), sort: browseSort });
    if (activeFilter.kind === "tags") params.set("tag", activeFilter.value);
    else if (activeFilter.kind === "countries") params.set("country", activeFilter.value);
    else params.set("language", activeFilter.value);
    endless(async (offset) => {
      const p = new URLSearchParams(params);
      p.set("offset", String(offset));
      const page = await api(`/api/radio/browse?${p}`);
      return page;
    }, {
      emptyMsg: `No stations for “${activeFilter.name}”.`,
      offline: renderBrowseOffline,
    });
  }

  async function renderBrowseOffline() {
    const rows = await api("/api/radio/presets").catch(() => null);
    if (!rows) return false;
    const fallback = activeFilter ? rows.filter((st) => matchesFilter(st, activeFilter)) : rows;
    if (!fallback.length) {
      setNotice("Radio Browser is unreachable and none of the built-in presets match this filter. Favorites are served locally and still play.");
      return true;
    }
    setNotice(`Radio Browser is unreachable — showing ${fallback.length} matching built-in preset${fallback.length === 1 ? "" : "s"} until it answers again.`);
    fallback.forEach((st) => {
      const row = stationRow(st, () => fallback);
      row.__station = st;
      listEl.appendChild(row);
    });
    statusEl.textContent = `${fallback.length} presets`;
    return true;
  }

  function matchesFilter(st, filter) {
    if (!filter) return true;
    const v = String(filter.value || "").toLowerCase();
    if (filter.kind === "tags" && (Array.isArray(st.tags) ? st.tags : []).some((t) => String(t).toLowerCase() === v)) return true;
    if (filter.kind === "countries" && String(st.countrycode || "").toUpperCase() === v) return true;
    if (filter.kind === "languages") return false;
    return false;
  }

  // ---- Search --------------------------------------------------------------
  async function doSearch(q) {
    sortWrap.replaceChildren();
    setFilterNote(null);
    newList();
    const name = q.startsWith("#") ? "" : q;
    const tag = q.startsWith("#") ? q.slice(1) : "";
    sortButtons(() => doSearch(q));
    endless(async (offset) => {
      const p = new URLSearchParams({ limit: String(PAGE), sort: browseSort });
      if (name) p.set("q", name);
      if (tag) p.set("tag", tag);
      return await api(`/api/radio/browse?${p}`);
    }, {
      emptyMsg: `No stations found for “${q}”.`,
      offline: renderBrowseOffline,
    });
  }

  let debounceT;
  searchInput.addEventListener("input", () => {
    clearTimeout(debounceT);
    debounceT = setTimeout(() => {
      const q = searchInput.value.trim();
      if (q) doSearch(q);
      else { const gen = newList(); setNotice(null); sortWrap.replaceChildren(); errEmpty(listEl, "Type to search Radio Browser — names, or #tag for a genre.", "📡"); void gen; }
    }, 300);
  });

  // ---- tabs ----------------------------------------------------------------
  tabs.querySelectorAll("button[data-t]").forEach((b) =>
    b.addEventListener("click", () => {
      closeStationMenu();
      tab = b.dataset.t;
      tabs.querySelectorAll("button[data-t]").forEach((x) => {
        const on = x === b;
        x.classList.toggle("active", on);
        x.setAttribute("aria-selected", on ? "true" : "false");
      });
      searchWrap.style.display = tab === "search" ? "flex" : "none";
      statusEl.textContent = "";
      if (tab === "hermes") loadHermes();
      if (tab === "popular") loadPopular();
      if (tab === "browse") loadBrowse();
      if (tab === "search") {
        sortWrap.replaceChildren();
        setFilterNote(null);
        setNotice(null);
        const gen = newList();
        if (searchInput.value.trim()) doSearch(searchInput.value.trim());
        else errEmpty(listEl, "Type to search Radio Browser — names, or #tag for a genre.", "📡");
        void gen;
      }
      if (tab === "favorites") loadFavorites();
    }));

  // ---- add a custom stream URL ----------------------------------------------
  addBtn.addEventListener("click", async () => {
    const url = await textPrompt("Add stream URL", { label: "Stream URL", placeholder: "https://…" });
    if (!url) return;
    const name = await textPrompt("Station name", { label: "Name", value: url.replace(/^https?:\/\//, "").slice(0, 40) });
    if (name === null) return;
    const st = { id: `custom-${Date.now()}`, name, url, homepage: "", tags: [], codec: "", bitrate: null, favicon: "", country: "", source: "custom" };
    try {
      await api("/api/radio/favorites", "POST", st);
      toast("Station saved to favorites", { kind: "ok" });
      window.revampPlayerApi.loadTracks([stationItem(st)], { mode: "replace", play: true });
    } catch (e) { toast(e.message, { kind: "error" }); }
  });

  window.addEventListener("scroll", () => closeStationMenu(), true);

  // init: favorites first so hearts are right everywhere, then the default tab
  try { const f = await api("/api/radio/favorites"); favIds = new Set(f.map((x) => x.id)); } catch { /* offline */ }
  await loadHermes();
}
