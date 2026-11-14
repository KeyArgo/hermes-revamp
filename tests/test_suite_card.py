"""The "Works well with REVamp" card: Aura and Music Studio, in Settings > REVamp only.

It stays inside the plugin SDK (the catalog review refuses DOM reach-ins and raw window bridges),
never installs anything, and every install command it copies is one Hermes accepts."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src() -> str:
    return (ROOT / "desktop/plugin.js").read_text(encoding="utf-8").replace("\r\n", "\n")


def _block(src: str, start: str) -> str:
    body = src[src.index(start):]
    return body[:body.index("\n}\n")]


def test_the_card_is_in_settings_and_nowhere_else():
    src = _src()
    assert "jsx(SuiteCard, {})" in _block(src, "function RevampSettings(")
    assert src.count("jsx(SuiteCard") == 1, "the suite card is shown only in Settings"


def test_the_card_lists_aura_and_music_studio_with_working_install_commands():
    suite = _block(_src(), "const SUITE = [")
    ids = re.findall(r"id: '([^']+)'", suite)
    assert ids == ["aura", "music-studio"]
    for cmd in re.findall(r"install: '([^']+)'", suite):
        # `hermes plugins install` (plural): `hermes plugin install` is not a command.
        assert re.fullmatch(r"hermes plugins install [A-Za-z0-9-]+(/[A-Za-z0-9._-]+)?", cmd), cmd
    for url in re.findall(r"repo: '([^']+)'", suite):
        assert url.startswith("https://github.com/KeyArgo/"), url


def test_the_card_uses_only_the_sdk():
    card = _block(_src(), "function SuiteCard(")
    assert "ctxRef?.os.openExternal(" in card and "ctxRef?.os.writeClipboard(" in card
    for banned in ("window.open", "window.hermesDesktop", "document.", "localStorage", "install(" ):
        assert banned not in card, banned


def test_copy_says_copied_only_when_it_copied():
    card = _block(_src(), "function SuiteCard(")
    assert "setCopied(ok ? item.id : '')" in card
