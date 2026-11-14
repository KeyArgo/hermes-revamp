"""The suite's version chip (VERSION-CHIP.md): one line, "v1.0.0-dev.342 · 1a2b3c4d · 2026-11-13",
the same on every surface (the player page, the Desktop status menu, Settings, a bug report).

The server builds the line (version_stamp.chip_line) and hands it out as ``line``; the page and the
Desktop bundle only fall back to building it themselves for an older server, in the same order."""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

from revamp_core import server, version_stamp

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    # A Windows checkout has CRLF endings; the function-end marker below is "\n}\n".
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_chip_line_is_version_then_short_commit_then_date():
    info = {"version": "1.0.0-dev.342", "commit": "1a2b3c4d5e6f7a8b9c0d", "commit_date": "2026-11-13T10:11:12-07:00",
            "dirty": False}
    assert version_stamp.chip_line(info) == "v1.0.0-dev.342 · 1a2b3c4d · 2026-11-13"


def test_chip_line_says_modified_for_an_uncommitted_tree():
    info = {"version": "1.0.0", "commit": "abcdef0123456789", "commit_date": "2026-11-13", "dirty": True}
    assert version_stamp.chip_line(info) == "v1.0.0 · abcdef01 · 2026-11-13 · modified"


def test_chip_line_without_git_is_just_the_version():
    assert version_stamp.chip_line({"version": "1.0.0"}) == "v1.0.0"


def test_describe_is_the_chip_line():
    assert version_stamp.describe() == version_stamp.chip_line(version_stamp.as_dict())


def test_api_version_carries_the_line_the_chips_show():
    info = server._stamp_dict()
    assert info["line"] == version_stamp.chip_line(info)
    assert info["line"].startswith("v" + info["version"])


def test_a_missing_stamp_still_answers_with_a_line(monkeypatch):
    monkeypatch.setattr(server, "_stamp", None)
    info = server._stamp_dict()
    assert info["line"] == "v" + info["version"]


def test_desktop_version_route_proxies_the_server(server, monkeypatch):  # noqa: F811 (the running-server fixture)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_plugin_api import _stub_fastapi
    _stub_fastapi()
    sys.modules.pop("dashboard.plugin_api", None)
    api = importlib.import_module("dashboard.plugin_api")
    resp = api.version()
    assert resp.status_code == 200, resp.body
    body = json.loads(resp.body)
    assert body["version"] and body["line"].startswith("v" + body["version"])

    monkeypatch.setenv("REVAMP_PORT", "1")  # nothing listens there
    assert api.version().status_code == 503  # a down server is no chip, never a guess


def _fallback_order(src: str) -> list[str]:
    """The order the fallback builder pushes its parts in: commit before date, then modified."""
    body = src[src.index("function buildLine("):]
    body = body[:body.index("\n}\n")]
    return list(dict.fromkeys(re.findall(r"commit_short|commit_date|modified", body)))  # first mention of each


def test_the_page_and_desktop_fallbacks_build_the_same_line():
    for rel in ("web/app.js", "desktop/plugin.js"):
        src = _src(rel)
        assert "build.line" in src, f"{rel} ignores the server's line"
        assert _fallback_order(src) == ["commit_short", "commit_date", "modified"], rel


def test_desktop_shows_the_chip_in_settings_and_the_status_menu():
    src = _src("desktop/plugin.js")
    assert "ctxRef.rest('/version'" in src, "the chip must read the running build, not a constant"
    settings = src[src.index("function RevampSettings("):src.index("function DuckSettings(")]
    assert "jsx(VersionChip, {})" in settings
    bar = src[src.index("function RevampBar("):]
    bar = bar[:bar.index("\n}\n")]
    assert "jsx(VersionChip, { compact: true })" in bar
