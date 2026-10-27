"""Menu names and the rule that choosing Frame never becomes Mini."""

from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "desktop" / "plugin.js"


def _source():
    return PLUGIN.read_text(encoding="utf-8")


def _between(src: str, start: str, end: str) -> str:
    a = src.index(start)
    return src[a:src.index(end, a)]


def test_location_menu_uses_the_five_names():
    src = _source()
    for key, label in (
        ("locMini", "Mini player"),
        ("locDocked", "Frame"),
        ("locFloating", "Pop into Hermes"),
        ("locDesktop", "Pop out to desktop"),
        ("locFull", "Full screen"),
    ):
        assert f"{key}: '{label}'" in src


def test_place_items_order_and_frame_side():
    body = _between(_source(), "function placeItems(", "function LocationRow(")
    assert body.index("item('mini'") < body.index("item('docked'")
    assert body.index("item('docked'") < body.index("item('floating'")
    assert body.index("item('floating'") < body.index("extra('desktop'")
    assert body.index("extra('desktop'") < body.index("extra('full'")
    # "Frame" + " (right)" / " (left)" from the saved side, not a different mode.
    assert "item('docked', 'locDocked', ` (${side})`)" in body


def test_placement_location_does_not_collapse_docked_to_mini():
    body = _between(_source(), "function placementLocation()", "const LOC_BUS")
    assert "return loc" in body
    assert "mini" not in body
    assert "DOCKED_MIN_WIDTH" not in body
    assert "innerWidth" not in body


def test_choosing_frame_reveals_the_pane():
    body = _between(_source(), "function setPlayerLocation(", "function dockedPlayerPane(")
    assert "if (loc === 'docked')" in body
    assert "host.revealPane?.(scopedPaneId($dockedPlayerSide.get()))" in body
