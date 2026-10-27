// REVamp for pi (and omp, which runs pi extensions).
//
// - Tells REVamp when the agent is working (the player's activity light and ticker).
// - Adds a `revamp` tool the model can use when you ask for music, and a `/revamp` command:
//     /revamp            what's playing
//     /revamp pause      play | pause | toggle | stop | next | prev
//     /revamp vol 40     volume
//     /revamp music bowie    play library matches
//     /revamp radio jazz     play a radio station
//
// Everything goes through the `revampctl` command (installed with REVamp), so nothing
// here talks to the network itself. REVAMP_TOOLS=0 skips the tool (omp already gets it
// over MCP).
// @ts-nocheck

import { execFile, spawn } from "node:child_process";

const SOURCE = process.env.REVAMP_SOURCE || (process.argv.some((a) => /\bomp\b/.test(a)) ? "omp" : "pi");
const WITH_TOOLS = process.env.REVAMP_TOOLS !== "0" && SOURCE !== "omp";

function ctl(args: string[], timeoutMs = 15000): Promise<string> {
  return new Promise((resolve) => {
    execFile("revampctl", args, { timeout: timeoutMs }, (err, stdout, stderr) => {
      const text = `${stdout || ""}${stderr || ""}`.trim();
      resolve(text || (err ? `revampctl failed: ${err.message}` : "done"));
    });
  });
}

// Fire and forget: never slow the agent down.
function maskSecrets(text: string): string {
  const pat = /((?:authorization|api[-_]?key|api[-_]?secret|token|secret|password|passwd|pwd|bearer|basic)[-_ ]?[=: ]*(?:bearer|basic)?[ \t]*)(?:[^\s'"]+)/gi;
  try {
    return text.replace(pat, (_all, head: string) => `${head}***`);
  } catch {
    return text;
  }
}

function event(kind: string, tool = "-", preview = "") {
  try {
    const child = spawn("revampctl", ["event", kind, tool, maskSecrets(preview).slice(0, 80)], {
      detached: true,
      stdio: "ignore",
      env: { ...process.env, REVAMP_SOURCE: SOURCE },
    });
    child.on("error", () => {});
    child.unref();
  } catch {
    // REVamp not installed: stay silent
  }
}

function argsFor(action: string, value: string): string[] | null {
  const a = (action || "status").toLowerCase();
  if (["play", "pause", "toggle", "stop", "next", "prev"].includes(a)) return [a];
  if (a === "status" || a === "now" || a === "") return ["status"];
  if (a === "vol" || a === "volume") {
    const n = parseInt(value, 10);
    return ["volume", String(Number.isNaN(n) ? 50 : Math.max(0, Math.min(100, n)))];
  }
  if (a === "music" || a === "queue") return value ? ["queue", value] : null;
  if (a === "radio") return value ? ["radio", value] : null;
  return null;
}

export default function (pi) {
  pi.on("session_start", async () => event("session_start"));
  pi.on("session_shutdown", async () => event("session_end"));
  pi.on("tool_execution_start", async (e) => {
    const preview = String(e?.args?.command || e?.args?.path || e?.args?.file_path || "");
    event("tool_start", String(e?.toolName || "-"), preview);
  });
  pi.on("tool_execution_end", async (e) => event("tool_end", String(e?.toolName || "-")));

  pi.registerCommand("revamp", {
    description: "REVamp music: /revamp [play|pause|next|prev|vol N|music WORDS|radio WORDS]",
    handler: async (args, ctx) => {
      const [action = "status", ...rest] = String(args || "").trim().split(/\s+/);
      const argv = argsFor(action, rest.join(" "));
      if (!argv) {
        ctx.ui.notify("Try: /revamp, /revamp pause, /revamp next, /revamp vol 40, /revamp music bowie, /revamp radio jazz", "info");
        return;
      }
      ctx.ui.notify(await ctl(argv), "info");
    },
  });

  if (!WITH_TOOLS) return;
  pi.registerTool({
    name: "revamp",
    label: "REVamp music",
    description:
      "Control the user's REVamp music player. Use only when the user asks about or for music. " +
      "action: status, play, pause, toggle, stop, next, prev, volume (value 0-100), music (value = search words), radio (value = station or genre).",
    parameters: {
      type: "object",
      required: ["action"],
      properties: {
        action: {
          type: "string",
          enum: ["status", "play", "pause", "toggle", "stop", "next", "prev", "volume", "music", "radio"],
        },
        value: { type: "string", description: "volume percent, or search words for music/radio" },
      },
    },
    async execute(_id, params) {
      const argv = argsFor(params?.action, String(params?.value || ""));
      const text = argv ? await ctl(argv) : "unknown action, or missing value";
      return { content: [{ type: "text", text }], details: {} };
    },
  });
}
