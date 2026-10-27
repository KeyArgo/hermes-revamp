// focus.js — Focus view: a steady generated drone with an optional gentle pulse.
// Talks only to the focus adapter (web/focus/adapter.js), so it keeps working
// when Focus moves behind the shared playback controller.
import { getFocus, FOCUS_TIMERS } from "../focus/adapter.js";

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const pct = (v, max) => `${Math.round((v / max) * 100)}%`;

export async function render(root, api) {
  root.innerHTML = "";
  const focus = getFocus(api);

  // Hero: one primary action, sized like a control rather than a billboard.
  const box = document.createElement("div");
  box.className = "glass focus-hero";
  const big = document.createElement("button");
  big.className = "btn btn-primary focus-start";
  const line = document.createElement("div");
  line.className = "muted focus-line";
  box.append(big, line);
  root.appendChild(box);
  big.addEventListener("click", () => {
    const st = focus.status().state;
    if (st === "stopped") focus.start(); else if (st === "paused") focus.resume(); else focus.stop();
  });

  const controls = document.createElement("div");
  controls.className = "glass focus-controls";
  root.appendChild(controls);

  const sliders = {};
  const values = {};
  // Label + live value on the top line; the track gets the full width below it,
  // so the slider is never squeezed into the right half of the row.
  const mkSlider = (key, label, min, max, step, ends, format) => {
    const row = document.createElement("div");
    row.className = "focus-row";

    const head = document.createElement("div");
    head.className = "focus-head";
    const lab = document.createElement("label");
    lab.textContent = label;
    const val = document.createElement("span");
    val.className = "focus-value";
    head.append(lab, val);

    const track = document.createElement("div");
    track.className = "focus-track";
    const lo = document.createElement("span"); lo.className = "focus-end"; lo.textContent = ends[0];
    const rng = document.createElement("input");
    Object.assign(rng, { type: "range", min, max, step });
    rng.dataset.focus = key;
    rng.setAttribute("aria-label", label);
    const hi = document.createElement("span"); hi.className = "focus-end"; hi.textContent = ends[1];
    track.append(lo, rng, hi);

    const paintValue = () => { val.textContent = format(Number(rng.value)); };
    rng.addEventListener("input", () => {
      paintValue();
      focus.set({ [key]: Number(rng.value) });
    });
    paintValue();

    row.append(head, track);
    sliders[key] = rng;
    values[key] = paintValue;
    return row;
  };
  controls.append(
    mkSlider("depth", "Pulse", 0, 0.6, 0.01, ["Off", "Strong"], (v) => (v === 0 ? "Off" : pct(v, 0.6))),
    mkSlider("warmth", "Tone", 0, 1, 0.01, ["Bright", "Dark"], (v) => pct(v, 1)),
    mkSlider("noise", "Noise", 0, 1, 0.01, ["None", "More"], (v) => (v === 0 ? "None" : pct(v, 1))),
  );

  // Timer: off / 25 / 50 / 90 minutes, then a gentle fade. Every option is a chip
  // of equal width so the group reads as one segmented control.
  const timerRow = document.createElement("div");
  timerRow.className = "focus-row focus-row--timer";
  const tl = document.createElement("div");
  tl.className = "focus-head";
  const tlLabel = document.createElement("label");
  tlLabel.textContent = "Timer";
  tl.appendChild(tlLabel);
  const tabs = document.createElement("div");
  tabs.className = "focus-tabs";
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
  adv.className = "focus-adv";
  const sum = document.createElement("summary");
  sum.textContent = "Advanced";
  adv.append(sum, mkSlider("rate", "Pulse rate", 12, 20, 0.5, ["12 Hz", "20 Hz"], (v) => `${v} Hz`));
  controls.appendChild(adv);

  const note = document.createElement("p");
  note.className = "muted focus-note";
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
      if (document.activeElement !== el) { el.value = s.params[k]; values[k](); } // don't fight a drag
    }
    for (const b of tabs.children) b.classList.toggle("active", Number(b.dataset.timer) === s.params.timer);
  };
  const off = focus.on(paint);
  paint(focus.status());
}
