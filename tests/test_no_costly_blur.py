"""Guard (2026-10-25): under software graphics (Hermes on many NVIDIA Linux hosts) a
backdrop-filter blur on the always-visible panels made the browser redraw the blur every
frame, ~1.2 CPU cores for an idle page. Only short-lived overlays (the modal) may blur."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"


def test_persistent_panels_do_not_blur_what_is_behind_them():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = m.group(1).strip(), m.group(2)
        if re.search(r"(?<!-)backdrop-filter\s*:\s*(?!none)", body):
            assert "modal" in selector, f"{selector} blurs the page behind it"


def test_milkdrop_starts_off_under_software_graphics():
    js = (WEB / "player.js").read_text(encoding="utf-8")
    assert re.search(r"swiftshader\|llvmpipe", js)
    assert "!(hasWebGL() && softwareGL)" in js


def test_idle_player_hides_the_always_redrawing_spectrum():
    # 2026-10-25: Webamp's spectrum canvas redrew every frame while stopped, ~1.2 cores of the
    # pop-out's cost under SwiftShader in the real Hermes. Hidden while not playing.
    js = (WEB / "player.js").read_text(encoding="utf-8")
    css = (WEB / "app.css").read_text(encoding="utf-8")
    assert 'classList.toggle("revamp-idle", status !== "PLAYING")' in js
    assert re.search(r"body\.revamp-idle #webamp #visualizer\s*\{\s*visibility:\s*hidden", css)
