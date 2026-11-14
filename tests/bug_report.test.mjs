import assert from "node:assert/strict";
import test from "node:test";

import {
  buildBugReportUrl,
  defaultBugTitle,
  formatBugReportBody,
  ISSUE_NEW_URL,
} from "../web/bug-report.js";

test("bug report body matches the GitHub template sections", () => {
  const body = formatBugReportBody({
    revampVersion: "1.0.0",
    revampBuild: "abc123",
    hermesVersion: "0.9.0",
    os: "Linux",
    browser: "Hermes",
    playerPlace: "Frame",
    skin: "classic",
    theme: "aurora",
  });
  assert.match(body, /^## What happened\?\n\n\n\n## What did you expect\?/);
  assert.match(body, /## Steps to reproduce\n\n\n\n## Environment/);
  assert.match(body, /- REVamp version: 1\.0\.0/);
  assert.match(body, /- REVamp build: abc123/);
  assert.match(body, /- Hermes version: 0\.9\.0/);
  assert.match(body, /- Player place: Frame/);
  assert.match(body, /- Skin: classic/);
  assert.match(body, /- Theme: aurora/);
  assert.match(body, /Please remove library paths/);
});

test("buildBugReportUrl opens the bug form, prefilled, with no report in the URL", () => {
  const url = buildBugReportUrl({
    revampVersion: "v1.0.0-dev.304 · 2026-11-14 · ac8db746",
    revampBuild: "b",
    os: "Windows",
    hermesVersion: "0.23.1",
    browser: "Chrome/146",
    playerPlace: "Mini player",
  });
  assert.ok(url.startsWith(ISSUE_NEW_URL));
  const u = new URL(url);
  // .github/ISSUE_TEMPLATE/bug_report.yml, filled by field id
  assert.equal(u.searchParams.get("template"), "bug_report.yml");
  assert.equal(u.searchParams.get("version"), "v1.0.0-dev.304 · 2026-11-14 · ac8db746");
  assert.match(u.searchParams.get("platform"), /Windows, Hermes 0\.23\.1/);
  // GitHub ignores body for a form (blank issues are off), and the details go to the clipboard
  assert.equal(u.searchParams.get("body"), null);
  assert.equal(typeof defaultBugTitle(), "string");
});

test("the bug form URL stays short even with long diagnostics", () => {
  // Windows silently fails to open an external URL past ~2,000 characters: the old URL carried the
  // whole report (diagnostics, stacks, user agent) and "Open GitHub issue" did nothing.
  const url = buildBugReportUrl({
    revampVersion: "v" + "9".repeat(500),
    os: "Windows ".repeat(100),
    browser: "Mozilla/5.0 ".repeat(100),
    logs: "x".repeat(20000),
    diagnostics: "stack ".repeat(5000),
  });
  assert.ok(url.length < 1000, `URL is ${url.length} characters`);
});
