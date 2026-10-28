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

test("buildBugReportUrl encodes title and body for GitHub", () => {
  const url = buildBugReportUrl({
    revampVersion: "2",
    revampBuild: "b",
    playerPlace: "Mini player",
    skin: "nous",
    theme: "paper",
  });
  assert.ok(url.startsWith(ISSUE_NEW_URL));
  const u = new URL(url);
  assert.equal(u.searchParams.get("title"), defaultBugTitle());
  const body = u.searchParams.get("body");
  assert.ok(body.includes("## What happened?"));
  assert.ok(body.includes("- REVamp build: b"));
  assert.ok(body.includes("- Player place: Mini player"));
});
