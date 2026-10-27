"""The mini player's right-click menu must reach the row (2026-10-25: it never opened in Hermes).

Hermes listens for 'contextmenu' on the window in the capture phase and stops the event,
except inside an element marked data-hermes-context-menu-trigger. Without the marker the
row's onContextMenu never runs.
"""
import re
from pathlib import Path

PLUGIN = (Path(__file__).resolve().parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")


def test_rows_with_a_menu_carry_the_hermes_marker():
    row = PLUGIN[PLUGIN.index("function Row("):]
    row = row[:row.index("\nfunction ", 1)]
    assert "onContextMenu: item ?" in row
    assert re.search(r"'data-hermes-context-menu-trigger':\s*item \? '' : undefined", row)
