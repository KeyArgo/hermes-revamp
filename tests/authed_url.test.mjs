import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

// Runs the real authedUrl() from web/app.js against a fake page location.
function loadAuthedUrl(href, token = "SECRET") {
  const src = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
  const start = src.indexOf("export function authedUrl(");
  const end = src.indexOf("\n}\n", start) + 2;
  const body = src.slice(start, end).replace("export ", "");
  return new Function("location", "TOKEN", `${body}; return authedUrl;`)({ href }, token);
}

test("the token never goes to another host", () => {
  const authedUrl = loadAuthedUrl("http://127.0.0.1:8114/?embed=1");
  assert.match(authedUrl("/api/media/abc"), /^http:\/\/127\.0\.0\.1:8114\/api\/media\/abc\?t=SECRET$/);
  for (const evil of ["//evil.example/x.mp3", "https://evil.example/a", "http://127.0.0.1:9999/x"]) {
    assert.doesNotMatch(authedUrl(evil), /SECRET/, evil);
  }
});
