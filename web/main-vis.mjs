// Main-window analyzer preferences.
//
// Webamp 2.3.1 does not take these as constructor options. The bar renderer
// reads them off one object (bandwidth, peaks, saFalloff, saPeakFalloff) and
// the mode is display.visualizerStyle: 0 spectrum, 1 oscilloscope, 2 off.
// A click on the analyzer dispatches TOGGLE_VISUALIZER_STYLE, which cycles
// those three. Falloff numbers are the same ones the renderer latches.

export const MAIN_VIS_MODES = ["BAR", "OSCILLOSCOPE", "NONE"];

export const MAIN_VIS_FALLOFF = {
  slower: 3,
  slow: 6,
  moderate: 12,
  fast: 16,
  faster: 32,
};

export const MAIN_VIS_PEAK_FALLOFF = {
  slower: 1.05,
  slow: 1.1,
  moderate: 1.2,
  fast: 1.4,
  faster: 1.6,
};

// Thick bands, a slow drop, and no peak caps. Webamp's own default is thick
// bands already ("wide"), with peaks on and a moderate drop, which reads as jitter.
export const MAIN_VIS_DEFAULTS = {
  mode: "BAR",
  bandwidth: "wide",
  peaks: false,
  saFalloff: "slower",
  saPeakFalloff: "slow",
};

function overlay(settings) {
  const raw = settings?.mainVisualizer;
  return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
}

// Saved fields win one key at a time. Anything missing or unrecognized stays
// at the calm default, and unrelated settings are not part of this object.
export function mainVisPrefs(settings) {
  const over = overlay(settings);
  const mode = MAIN_VIS_MODES.includes(over.mode) ? over.mode : MAIN_VIS_DEFAULTS.mode;
  const bandwidth = over.bandwidth === "thin" || over.bandwidth === "wide"
    ? over.bandwidth
    : MAIN_VIS_DEFAULTS.bandwidth;
  const peaks = typeof over.peaks === "boolean" ? over.peaks : MAIN_VIS_DEFAULTS.peaks;
  const saFalloff = Object.prototype.hasOwnProperty.call(MAIN_VIS_FALLOFF, over.saFalloff)
    ? over.saFalloff
    : MAIN_VIS_DEFAULTS.saFalloff;
  const saPeakFalloff = Object.prototype.hasOwnProperty.call(MAIN_VIS_PEAK_FALLOFF, over.saPeakFalloff)
    ? over.saPeakFalloff
    : MAIN_VIS_DEFAULTS.saPeakFalloff;
  return { mode, bandwidth, peaks, saFalloff, saPeakFalloff };
}

export function mainVisFalloff(prefs) {
  return MAIN_VIS_FALLOFF[prefs.saFalloff];
}

export function mainVisPeakFalloff(prefs) {
  return MAIN_VIS_PEAK_FALLOFF[prefs.saPeakFalloff];
}

// Patch for setSavedSettings. Only the mode key changes; peaks, falloff, and
// any other saved analyzer fields stay. Null when there is nothing to write.
export function mainVisModePatch(settings, mode) {
  if (!MAIN_VIS_MODES.includes(mode)) return null;
  const base = overlay(settings);
  if (base.mode === mode) return null;
  return { mainVisualizer: { ...base, mode } };
}
