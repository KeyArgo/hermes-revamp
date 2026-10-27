# Contributing to REVamp

Use Python 3 and work from a checkout. REVamp’s core uses the standard library; do not add a dependency unless the project explicitly adopts it.

Run the Python suite with `pytest`. Browser-facing regression suites are included in the same pytest command; use a local Chrome/Chromium when a test asks for it. Capture release screenshots with `node tools/screenshots.mjs`; it uses a temporary REVamp data directory and `lanes/_e2e/lib`, never a real music folder.

Keep changes readable, preserve existing behavior, add tests for behavior changes, and run `pytest` before opening a pull request. Do not add tokens, library paths, or private media metadata to fixtures, screenshots, or issues.
