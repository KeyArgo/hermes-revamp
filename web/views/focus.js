// focus.js — Focus view: a steady generated drone with an optional gentle pulse.
// Talks only to the focus adapter (web/focus/adapter.js), so it keeps working
// when Focus moves behind the shared playback controller.
import { getFocus, FOCUS_TIMERS } from "../focus/adapter.js";

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export async function render(root, api) {
  root.innerHTML = "";
  const focus = getFocus(api);

  const box = document.createElement("div");
  box.className = "glass focus-box";
  box.style.cssText = "padding:16px;margin-bottom:14px;text-align:center";
  const big = document.createElement("button");
  big.className = "btn btn-primary focus-start";
  big.style.cssText = "font-size:18px;padding:12px 36px;border-radius:24px";
  const line = document.createElement("div");
  line.className = "muted focus-line";
  line.style.marginTop = "10px";
  box.append(big, line);
  root.appendChild(box);
  big.addEventListener("click", () => {
    const st = focus.status().state;
    if (st === "stopped") focus.start(); else if (st === "paused") focus.resume(); else focus.stop();
  });

  const controls = document.createElement("div");
  controls.className = "glass";
  controls.style.cssText = "padding:4px 14px;margin-bottom:14px";
  root.appendChild(controls);

  const sliders = {};
  const mkSlider = (key, label, min, max, step, ends, hint) => {
    const row = document.createElement("div");
    row.className = "setting-row";
    const lab = document.createElement("label");
    lab.textContent = label;
    if (hint) { const h = document.createElement("div"); h.className = "hint"; h.textContent = hint; lab.appendChild(h); }
    const lo = document.createElement("span"); lo.className = "muted"; lo.textContent = ends[0];
    const rng = document.createElement("input");
    Object.assign(rng, { type: "range", min, max, step });
    rng.dataset.focus = key;
    rng.setAttribute("aria-label", label);
    const hi = document.createElement("span"); hi.className = "muted"; hi.textContent = ends[1];
    rng.addEventListener("input", () => focus.set({ [key]: Number(rng.value) }));
    row.append(lab, lo, rng, hi);
    sliders[key] = rng;
    return row;
  };
  controls.append(
    mkSlider("depth", "Pulse", 0, 0.6, 0.01, ["Off", "Strong"]),
    mkSlider("warmth", "Tone", 0, 1, 0.01, ["Bright", "Dark"]),
    mkSlider("noise", "Noise", 0, 1, 0.01, ["None", "More"]),
  );

  // Timer: off / 25 / 50 / 90 minutes, then a gentle fade.
  const timerRow = document.createElement("div");
  timerRow.className = "setting-row";
  const tl = document.createElement("label"); tl.textContent = "Timer";
  const tabs = document.createElement("div"); tabs.className = "tabs";
  for (const m of FOCUS_TIMERS) {
    const b = document.createElement("button");
    b.dataset.timer = String(m);
    b.textContent = m ? `${m} min` : "Off";
    b.addEventListener("click", () => focus.setTimer(m));
    tabs.appendChild(b);
  }
  timerRow.append(tl, tabs);
  controls.appendChild(timerRow);

  const adv = document.createElement("details");
  adv.style.cssText = "padding:8px 0";
  const sum = document.createElement("summary"); sum.className = "muted"; sum.textContent = "Advanced";
  adv.append(sum, mkSlider("rate", "Pulse rate", 12, 20, 0.5, ["12 Hz", "20 Hz"]));
  controls.appendChild(adv);

  const note = document.createElement("p");
  note.className = "muted";
  note.style.padding = "0 4px";
  note.textContent = "The pulse is a soft, fast rise and fall in loudness. It may help some people focus; turn the pulse down if you notice it.";
  root.appendChild(note);

  const paint = (s) => {
    if (!root.isConnected) { off(); return; } // view switched away
    big.textContent = s.state === "playing" ? "Stop" : s.state === "paused" ? "Resume" : "Start Focus";
    big.dataset.state = s.state;
    line.textContent = s.state === "stopped"
      ? (s.endedBy === "timer" ? "Timer finished." : "A steady drone with no melody or beat.")
      : `${s.state === "paused" ? "Paused" : "Playing"} · ${fmt(s.elapsed)}${s.timerTotal ? ` · ${fmt(s.timerLeft)} left` : ""}`;
    for (const [k, el] of Object.entries(sliders)) {
      if (document.activeElement !== el) el.value = s.params[k]; // don't fight a drag
    }
    for (const b of tabs.children) b.classList.toggle("active", Number(b.dataset.timer) === s.params.timer);
  };
  const off = focus.on(paint);
  paint(focus.status());
}
