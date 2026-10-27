"""revampctl's one-line status (pi, omp, Codex, opencode, agy and Cursor all show it)."""
import importlib.machinery
import importlib.util
from pathlib import Path

CTL = Path(__file__).resolve().parents[1] / "bin" / "revampctl"


def load():
    loader = importlib.machinery.SourceFileLoader("revampctl_status_test", str(CTL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def state(status, title="Song", artist="Band"):
    return {"live": {"player_connected": True,
                     "now_playing": {"status": status, "title": title, "artist": artist}}}


def test_status_follows_the_player_in_any_case():
    ctl = load()
    assert ctl.now_line(state("PLAYING")) == "Playing: Song — Band"
    assert ctl.now_line(state("playing")) == "Playing: Song — Band"
    assert ctl.now_line(state("PAUSED")) == "Paused: Song — Band"
    assert ctl.now_line(state("STOPPED")) == "Stopped: Song — Band"
    assert ctl.now_line(state(None, artist=None)) == "Stopped: Song"


def test_status_without_a_player_says_so():
    ctl = load()
    assert ctl.now_line({"live": {"player_connected": False}}).startswith("Revamp: no player open")
