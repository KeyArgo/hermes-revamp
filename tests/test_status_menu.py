"""The status-bar menu ("golden hour"): what's playing and a console of 3D keys on top, pill tabs
to browse, the player's places as pills, and the small print with the build chip.

Every control the old menu had is still here; these pin that, and that the look stays scoped to
the menu (the CSS only reaches .revamp-shell, never Hermes's own chrome)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "desktop/plugin.js").read_text(encoding="utf-8").replace("\r\n", "\n")


def _css() -> str:
    start = SRC.index("const CSS = `") + len("const CSS = `")
    return SRC[start:SRC.index("`\n", start)]


def _fn(name: str) -> str:
    body = SRC[SRC.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def test_the_menu_is_stage_browse_places_foot():
    bar = _fn("RevampBar")
    order = [bar.index(s) for s in ("jsx(Hero, { np })", "jsx(Console, { np })", "jsx(Picker, { np })",
                                     "jsx(LocationRowWithTip, {})", "jsx(ReportBugRow, { compact: true })",
                                     "jsx(VersionChip, { compact: true })")]
    assert order == sorted(order)
    assert "revamp-shell" in bar and "jsx(QueuePanel, { np })" in bar


def test_the_console_keeps_every_transport_action():
    console = _fn("Console")
    for action in ("command('prev')", "command('next')", "onClick: toggle", "command('shuffle'", "command('repeat'",
                   "command('seek_by', { seconds: -10 })", "command('seek_by', { seconds: 10 })", "command('rate'",
                   "setVolume(", "bringBackFromDesktop()", "engine?.setWinamp(", "host.navigate(ROUTE)"):
        assert action in console, action
    assert "big: true" in console, "the play key is the big round one"


def test_tabs_and_places_are_pills():
    assert "jsx(PillTabs, {" in _fn("Picker")
    assert "SegmentedControl" not in _fn("Picker")
    places = _fn("LocationRow")
    assert "className: 'revamp-place'" in places and "'aria-pressed': on" in places


def test_the_look_is_scoped_to_the_menu():
    css = _css()
    block = re.sub(r"/\*.*?\*/", "", css[css.index("golden hour"):], flags=re.S)
    block = block[block.index(".revamp-panel"):]
    selectors = [s.strip() for rule in re.findall(r"([^{}]+)\{", block) for s in rule.split(",")]
    selectors = [s for s in selectors if s and not s.startswith("@") and not re.match(r"^(to|\d+%)$", s)]
    stray = [s for s in selectors if not s.startswith(".revamp-")]
    assert not stray, f"rules reaching outside REVamp's own classes: {stray}"


def test_motion_respects_reduced_motion():
    css = _css()
    for anim in ("revamp-spin", "revamp-glow"):
        assert f"animation:{anim}" in css
    assert css.count("prefers-reduced-motion:reduce") >= 2


def test_no_invalid_font_shorthand():
    """`font: 600 11px/26px inherit` is invalid CSS: the whole declaration is dropped (the tabs
    rendered at the browser's default size in the first build)."""
    assert not re.search(r"font:[^;}]*\binherit\b", _css().split("golden hour", 1)[1])
