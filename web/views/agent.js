// agent.js — Hermes event log + settings: duck on approval, duck level, ticker on/off.
import { buildBugReportUrl, collectWebBugEnv, formatBugReportBody } from "../bug-report.js";
import { getHostPlayerPlace } from "../host-place.js";
import { getThemeSnapshot } from "../theme.js";
import { toast } from "./modal.js";
import { mountBackups } from "./backups.js";

const KIND_ORDER = ["approval_request", "tool_start", "tool_end", "session_start", "session_end", "approval_response"];
const kindIcon = (k, ok) =>
  k === "tool_end" ? (ok ? "✓" : "✗") :
  k === "approval_request" ? "⏸" :
  k === "session_start" ? "▶" : k === "session_end" ? "■" : "⚙";

export async function render(root, api) {
  root.innerHTML = "";
  const pApi = window.revampPlayerApi;

  // ---- settings
  const settings = pApi?.getSettings?.() || {};
  const box = document.createElement("div");
  box.className = "glass";
  box.style.cssText = "padding:12px 14px;margin-bottom:14px";

  const mkToggle = (label, hint, checked, onChange) => {
    const row = document.createElement("div");
    row.className = "setting-row";
    const lab = document.createElement("label");
    lab.innerHTML = `${label}<div class="hint"></div>`;
    lab.querySelector(".hint").textContent = hint;
    const sw = document.createElement("label");
    sw.className = "switch";
    const input = document.createElement("input");
    input.type = "checkbox"; input.checked = !!checked;
    input.setAttribute("aria-label", label);
    const track = document.createElement("span"); track.className = "track";
    const knob = document.createElement("span"); knob.className = "knob";
    input.addEventListener("change", () => onChange(input.checked));
    sw.append(input, track, knob);
    row.append(lab, sw);
    return { row, input };
  };

  const mkSlider = (label, value, min, max, step, onInput) => {
    const row = document.createElement("div");
    row.className = "setting-row";
    const lab = document.createElement("label"); lab.textContent = label;
    const val = document.createElement("span"); val.className = "muted"; val.textContent = `${Math.round(value * 100)}%`;
    const rng = document.createElement("input");
    rng.type = "range"; rng.min = min; rng.max = max; rng.step = step; rng.value = value;
    rng.setAttribute("aria-label", label);
    rng.addEventListener("input", () => { val.textContent = `${Math.round(rng.value * 100)}%`; onInput(Number(rng.value)); });
    row.append(lab, rng, val);
    return row;
  };

  let saveT;
  const persist = (patch) => {
    pApi?.setSavedSettings?.(patch);
  };

  box.appendChild(mkToggle("Chime and dip for approvals", "Briefly chime and lower music for a new approval you can act on.", settings.duck_on_approval === true,
    (v) => persist({ duck_on_approval: v })).row);
  box.appendChild(mkToggle("Chime", "Play a short two-tone chime with the dip.", settings.duck_chime !== false,
    (v) => persist({ duck_chime: v })).row);
  box.appendChild(mkSlider("Dip length", settings.duck_dip_seconds ?? 2, 1, 5, 1, (v) => persist({ duck_dip_seconds: v })));
  box.appendChild(mkSlider("Duck level", settings.duck_level ?? 0.25, 0.05, 1, 0.05, (v) => persist({ duck_level: v })));
  root.appendChild(box);

  const tickerToggle = mkToggle("Show agent ticker", "Bottom strip with live tool activity.", settings.ticker !== false, (v) => {
    persist({ ticker: v });
    const t = document.getElementById("ticker");
    if (t) t.style.display = v ? "flex" : "none";
  });
  const tickerRow = tickerToggle.row;
  tickerRow.style.borderBottom = "none";
  box.appendChild(tickerRow);

  const bugBox = document.createElement("div");
  bugBox.className = "setting-row";
  bugBox.style.flexDirection = "column";
  bugBox.style.alignItems = "flex-start";
  bugBox.style.gap = "8px";
  bugBox.style.marginTop = "10px";
  bugBox.style.paddingTop = "10px";
  bugBox.style.borderTop = "1px solid var(--line)";
  const bugLab = document.createElement("label");
  bugLab.textContent = "Report a bug";
  const bugHint = document.createElement("div");
  bugHint.className = "hint";
  bugHint.textContent = "Opens a GitHub issue with your environment filled in. Nothing is sent until you submit it there.";
  bugLab.appendChild(bugHint);
  const bugActs = document.createElement("div");
  bugActs.style.display = "flex";
  bugActs.style.gap = "8px";
  bugActs.style.flexWrap = "wrap";
  const openBug = document.createElement("button");
  openBug.type = "button";
  openBug.className = "btn sm";
  openBug.textContent = "Open GitHub issue";
  const copyBug = document.createElement("button");
  copyBug.type = "button";
  copyBug.className = "btn sm btn-ghost";
  copyBug.textContent = "Copy details";
  const envForBug = () =>
    collectWebBugEnv({
      apiGet: api,
      getTheme: getThemeSnapshot,
      getPlace: getHostPlayerPlace,
    });
  openBug.addEventListener("click", async () => {
    const env = await envForBug();
    window.open(buildBugReportUrl(env), "_blank", "noopener,noreferrer");
  });
  copyBug.addEventListener("click", async () => {
    const env = await envForBug();
    const text = formatBugReportBody(env);
    try {
      await navigator.clipboard.writeText(text);
      toast("Bug details copied", { kind: "ok" });
    } catch {
      toast("Could not copy to the clipboard", { kind: "error" });
    }
  });
  bugActs.append(openBug, copyBug);
  bugBox.append(bugLab, bugActs);
  box.appendChild(bugBox);

  // apply saved ticker visibility on load
  {
    const t = document.getElementById("ticker");
    if (t && settings.ticker === false) t.style.display = "none";
  }

  // ---- data & backups (list / snapshot / restore / export / import)
  root.appendChild(await mountBackups(root, api));

  // ---- event log
  const h = document.createElement("h3");
  h.textContent = "Event log";
  h.style.cssText = "margin:6px 0 8px;font-size:14px";
  root.appendChild(h);

  const filter = document.createElement("select");
  filter.className = "btn sm";
  filter.style.marginBottom = "8px";
  filter.setAttribute("aria-label", "Filter by kind");
  filter.innerHTML = `<option value="">All events</option>` +
    KIND_ORDER.map((k) => `<option value="${k}">${k}</option>`).join("");
  root.appendChild(filter);

  const log = document.createElement("div");
  log.className = "glass";
  log.style.cssText = "padding:4px 8px;max-height:50vh;overflow:auto";
  root.appendChild(log);

  let events = [];
  async function load() {
    try {
      const data = await api("/api/agent_events");
      events = Array.isArray(data) ? data : [];
    } catch (e) {
      log.innerHTML = `<div class="empty">Cannot load events: ${e.message}</div>`;
      return;
    }
    renderLog();
  }
  function renderLog() {
    const kind = filter.value;
    const rows = kind ? events.filter((e) => e.kind === kind) : events;
    log.innerHTML = "";
    if (!rows.length) {
      const d = document.createElement("div");
      d.className = "empty";
      d.textContent = "No agent events yet — events appear as Hermes runs tools.";
      log.appendChild(d);
      return;
    }
    const tbl = document.createElement("table"); tbl.className = "list events";
    const tb = document.createElement("tbody");
    for (const ev of rows.slice().reverse()) {
      const tr = document.createElement("tr");
      const t = document.createElement("td"); t.className = "num";
      t.textContent = ev.ts ? new Date(ev.ts * 1000 || ev.ts).toLocaleTimeString() : "";
      const i = document.createElement("td"); i.textContent = kindIcon(ev.kind, ev.ok);
      i.style.width = "24px";
      const k = document.createElement("td"); k.textContent = ev.kind;
      const tool = document.createElement("td"); tool.textContent = ev.tool || "";
      const prev = document.createElement("td"); prev.className = "muted";
      prev.textContent = (ev.preview || "").slice(0, 80);
      prev.style.maxWidth = "260px";
      tr.append(t, i, k, tool, prev);
      tr.style.color = ev.kind === "tool_end" && !ev.ok ? "var(--err)" : "";
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    log.appendChild(tbl);
  }
  filter.addEventListener("change", renderLog);

  // live updates while the view is open (poll SSE-fed ring buffer lightly)
  const poll = setInterval(() => {
    if (!document.body.contains(log)) return clearInterval(poll);
    if (document.hidden) return;
    load();
  }, 4000);
  const onShow = () => {
    if (!document.body.contains(log)) {
      document.removeEventListener("visibilitychange", onShow);
      return;
    }
    if (!document.hidden) load();
  };
  document.addEventListener("visibilitychange", onShow);
  await load();
}
