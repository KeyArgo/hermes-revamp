# Contributing to REVamp

Use Python 3 and work from a checkout. The core uses the standard library; do not add a dependency unless the project adopts it.

Run the Python suite with `pytest`. Browser-facing regression suites run in the same command; use a local Chrome or Chromium when a test asks for one. The line used for releases is:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider --deselect tests/test_nav_rail_e2e.py
```

Capture release screenshots with `node tools/screenshots.mjs`. It boots the real server against a temporary data directory and generated sine-wave audio, so it never touches a real music folder. Set `CHROME_BIN` when Chrome is not on the default path.

Keep changes readable, preserve existing behavior, add tests for behavior changes, and run the suite before opening a pull request. Do not add tokens, library paths or private media metadata to fixtures, screenshots or issues.
