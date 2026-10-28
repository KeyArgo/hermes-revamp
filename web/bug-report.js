// Prefilled GitHub bug reports (opens in the browser; nothing is sent automatically).

export const ISSUE_NEW_URL = "https://github.com/KeyArgo/hermes-revamp/issues/new";

export function defaultBugTitle() {
  return "[REVamp] ";
}

/** @param {Record<string, string | undefined>} env */
export function formatBugReportBody(env) {
  const lines = [
    "## What happened?",
    "",
    env.whatHappened ?? "",
    "",
    "## What did you expect?",
    "",
    env.whatExpected ?? "",
    "",
    "## Steps to reproduce",
    "",
    env.steps ?? "",
    "",
    "## Environment",
    "",
    `- REVamp version: ${env.revampVersion ?? "unknown"}`,
    `- REVamp build: ${env.revampBuild ?? "unknown"}`,
  ];
  if (env.hermesVersion) lines.push(`- Hermes version: ${env.hermesVersion}`);
  lines.push(
    `- Operating system: ${env.os ?? "unknown"}`,
    `- Browser (if relevant): ${env.browser ?? "n/a"}`,
    `- Player place: ${env.playerPlace ?? "unknown"}`,
    `- Skin: ${env.skin ?? "unknown"}`,
    `- Theme: ${env.theme ?? "unknown"}`,
    "",
    "## Logs or screenshots",
    "",
    env.logs ?? "",
    "",
    "Please remove library paths, tokens, and personal media names before posting.",
  );
  return lines.join("\n");
}

/** @param {Record<string, string | undefined>} env */
export function buildBugReportUrl(env, options = {}) {
  const title = options.title ?? defaultBugTitle();
  const body = options.body ?? formatBugReportBody(env);
  const u = new URL(ISSUE_NEW_URL);
  u.searchParams.set("title", title);
  u.searchParams.set("body", body);
  return u.toString();
}

export async function fetchRevampHealth() {
  try {
    return await fetch("/api/health", { cache: "no-store" }).then((r) => r.json());
  } catch {
    return {};
  }
}

/** @param {{ apiGet?: (path: string) => Promise<unknown>, getTheme?: () => string, getPlace?: () => string, hermesVersion?: string }} deps */
export async function collectWebBugEnv(deps = {}) {
  const health = await fetchRevampHealth();
  let skin = "unknown";
  if (deps.apiGet) {
    try {
      const state = await deps.apiGet("/api/state");
      if (state && typeof state === "object" && state.skin) skin = String(state.skin);
    } catch { /* offline */ }
  }
  const os =
    typeof navigator !== "undefined" && navigator.userAgentData?.platform
      ? navigator.userAgentData.platform
      : typeof navigator !== "undefined"
        ? navigator.platform || "unknown"
        : "unknown";
  const browser =
    typeof navigator !== "undefined" ? (navigator.userAgent || "n/a").slice(0, 160) : "n/a";
  return {
    revampVersion: health.version ? String(health.version) : "unknown",
    revampBuild: health.build ? String(health.build) : "unknown",
    hermesVersion: deps.hermesVersion,
    os,
    browser,
    playerPlace: deps.getPlace?.() || "full player page",
    skin,
    theme: deps.getTheme?.() || "unknown",
  };
}
