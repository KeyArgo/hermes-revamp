"""The approval dip (and its pop-up) only happens while music is actually playing."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "web" / "player.js").read_text(encoding="utf-8")


def test_duck_returns_early_unless_playing():
    body = SRC[SRC.index("function duckTo("):SRC.index("function unduck(")]
    guard = body.index('!== "PLAYING") return;')
    assert guard < body.index("toast(`Music turned down"), "the pop-up must come after the playing check"
    assert guard < body.index("duck.active = true")


def test_popup_still_has_its_own_switch():
    assert "saved.settings.duck_toast !== false" in SRC
