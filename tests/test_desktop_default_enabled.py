"""Installing REVamp should show it: the Desktop half is enabled by default (users can still turn it off)."""
from pathlib import Path


def test_desktop_plugin_is_enabled_by_default():
    src = (Path(__file__).resolve().parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "defaultEnabled: true" in src
    assert "defaultEnabled: false" not in src
