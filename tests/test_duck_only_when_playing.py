"""The approval alert only happens while music is actually playing."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "web" / "player.js").read_text(encoding="utf-8")


def test_duck_returns_early_unless_playing():
    body = SRC[SRC.index("function duckTo("):SRC.index("function unduck(")]
    guard = body.index('!== "PLAYING") return;')
    assert guard < body.index("playApprovalChime()"), "the chime must come after the playing check"
    assert guard < body.index("duck.active = true")


def test_chime_has_its_own_switch():
    assert "saved.settings.duck_chime === false" in SRC
