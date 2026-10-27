"""Every icon the desktop plugin uses must exist in the installed Hermes's icon set.

A missing one renders as `undefined`, which crashes Hermes's whole interface (React error #130)
when it sits in a host-drawn slot such as a pane header."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "desktop" / "plugin.js"
HERMES_ICONS = Path.home() / ".hermes" / "hermes-agent" / "apps" / "desktop" / "src" / "lib" / "icons.ts"


@pytest.mark.skipif(not HERMES_ICONS.exists(), reason="no Hermes source checkout to compare against")
def test_every_icon_exists_in_hermes():
    used = set(re.findall(r"icons\.([A-Za-z0-9]+)", PLUGIN.read_text(encoding="utf-8")))
    src = HERMES_ICONS.read_text(encoding="utf-8")
    missing = sorted(n for n in used if not re.search(rf"\b{n}\b", src))
    assert used and not missing, f"icons Hermes doesn't export: {missing}"
