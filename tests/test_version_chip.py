"""The version chip, in the suite's on-screen format (as Aura and Game Center show it):
"v1.0.0-dev.342 · 2026-11-13 · 1a2b3c4d", plus " · modified" for an uncommitted tree.

It appears on the player page's header, beside the title in Settings > REVamp, and (compact:
version and short commit) in the status-bar player menu. Every one reads the running server's
stamp, never a number compiled into a bundle."""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

from revamp_core import server

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    # A Windows checkout has CRLF endings; the function-end marker below is "\n}\n".
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def _function(src: str, name: str) -> str:
    body = src[src.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def _order(body: str) -> list[str]:
    """The parts a chip builder adds, in the order it adds them (first mention of each)."""
    return list(dict.fromkeys(re.findall(r"commit_date|commit_short|modified", body)))


def test_page_and_desktop_build_the_suite_line():
    for rel in ("web/app.js", "desktop/plugin.js"):
        body = _function(_src(rel), "buildLine")
        assert _order(body) == ["commit_date", "commit_short", "modified"], rel
        assert "slice(0, 10)" in body and "slice(0, 8)" in body, f"{rel}: date is YYYY-MM-DD, commit is 8 characters"
        assert "' · modified'" in body or '" · modified"' in body, rel


def test_the_chip_never_shows_a_compiled_in_version():
    page = _src("web/app.js")
    desktop = _src("desktop/plugin.js")
    assert 'apiGet("/api/version")' in page
    assert "bridge.request('/version'" in desktop and "ctxRef?.rest('/version'" in desktop
    assert not re.search(r"['\"]v?1\.0\.0(-dev[.\d]*)?['\"]", _function(desktop, "VersionChip"))


def test_desktop_shows_the_chip_in_settings_and_the_status_menu():
    src = _src("desktop/plugin.js")
    assert "jsx(VersionChip, {})" in _function(src, "RevampSettings")
    assert "jsx(VersionChip, { compact: true })" in _function(src, "RevampBar")


def test_api_version_has_every_part_the_chip_shows():
    info = server._stamp_dict()
    for key in ("version", "commit", "commit_short", "commit_date", "dirty", "channel"):
        assert key in info, key
    assert info["version"].startswith(server.VERSION)


def test_desktop_version_route_proxies_the_server(server, monkeypatch):  # noqa: F811 (the running-server fixture)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_plugin_api import _stub_fastapi
    _stub_fastapi()
    sys.modules.pop("dashboard.plugin_api", None)
    api = importlib.import_module("dashboard.plugin_api")
    resp = api.version()
    assert resp.status_code == 200, resp.body
    assert json.loads(resp.body)["version"]

    monkeypatch.setenv("REVAMP_PORT", "1")  # nothing listens there
    assert api.version().status_code == 503  # a down server is no chip, never a guess
