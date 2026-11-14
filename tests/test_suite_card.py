"""The "Works well with REVamp" card: Aura and Music Studio, in Settings > REVamp only.

It sends people to Hermes's own places: Capabilities > Plugins (host.navigate, the route Hermes's
command palette uses) and each plugin's Hermes Marketplace page once it is listed. It stays inside
the plugin SDK (the catalog review refuses DOM reach-ins and raw window bridges) and never installs
anything."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src() -> str:
    return (ROOT / "desktop/plugin.js").read_text(encoding="utf-8").replace("\r\n", "\n")


def _block(src: str, start: str) -> str:
    body = src[src.index(start):]
    return body[:body.index("\n}\n") if "\n}\n" in body else body.index("\n]\n")]


def _suite() -> list[dict]:
    src = _src()
    body = src[src.index("const SUITE = ["):]
    body = body[:body.index("\n]\n")]
    entries = []
    for chunk in body.split("\n  {")[1:]:
        entries.append({
            "id": re.search(r"id: '([^']+)'", chunk).group(1),
            "catalogName": re.search(r"catalogName: '([^']+)'", chunk).group(1),
            "listed": re.search(r"listed: (true|false)", chunk).group(1) == "true",
            "repo": re.search(r"repo: '([^']+)'", chunk).group(1),
        })
    return entries


def test_the_card_is_in_settings_and_nowhere_else():
    src = _src()
    assert "jsx(SuiteCard, {})" in _block(src, "function RevampSettings(")
    assert src.count("jsx(SuiteCard") == 1, "the suite card is shown only in Settings"


def test_the_card_lists_aura_and_music_studio_by_their_catalog_names():
    suite = _suite()
    assert [s["id"] for s in suite] == ["aura", "music-studio"]
    # The names their catalog entries use (plugin-catalog/aura.yaml, hermes-music-studio.yaml).
    assert [s["catalogName"] for s in suite] == ["aura", "hermes-music-studio"]
    for s in suite:
        assert re.fullmatch(r"KeyArgo/[A-Za-z0-9._-]+", s["repo"]), s


def test_find_in_plugins_opens_hermes_capabilities_plugins():
    src = _src()
    assert "const PLUGINS_ROUTE = '/capabilities?tab=plugins&plugin='" in src
    card = _block(src, "function SuiteCard(")
    assert "host.navigate(PLUGINS_ROUTE + encodeURIComponent(item.catalogName))" in card
    assert "'Find in Plugins'" in card


def test_listed_plugins_use_the_marketplace_and_the_catalog_install():
    src = _src()
    assert "const MARKETPLACE = 'https://hermes-agent.nousresearch.com/marketplace/'" in src
    install = src[src.index("const suiteInstall"):].split("\n", 1)[0]
    page = src[src.index("const suitePage"):].split("\n", 1)[0]
    # `hermes plugins install` (plural): `hermes plugin install` is not a command.
    assert "`hermes plugins install ${item.listed ? item.catalogName : item.repo}`" in install
    assert "item.listed ? MARKETPLACE + item.catalogName : `https://github.com/${item.repo}`" in page


def test_the_card_uses_only_the_sdk():
    card = _block(_src(), "function SuiteCard(")
    assert "ctxRef?.os.openExternal(" in card and "ctxRef?.os.writeClipboard(" in card
    for banned in ("window.open", "window.hermesDesktop", "document.", "localStorage", "querySelector"):
        assert banned not in card, banned


def test_copy_says_copied_only_when_it_copied():
    card = _block(_src(), "function SuiteCard(")
    assert "setCopied(ok ? item.id : '')" in card
