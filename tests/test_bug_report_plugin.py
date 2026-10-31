"""Bug report helpers in the Hermes plugin stay aligned with web/bug-report.js."""
import re

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = (REPO / "desktop" / "plugin.js").read_text(encoding="utf-8")
WEB = (REPO / "web" / "bug-report.js").read_text(encoding="utf-8")


def test_plugin_exposes_report_bug_in_settings_and_status_menu():
    assert "reportBug" in PLUGIN
    assert "jsx(ReportBugRow" in PLUGIN
    assert "openBugReport" in PLUGIN
    assert re.search(r"ctx(Ref)?\.os\.openExternal", PLUGIN)
    assert re.search(r"ctx(Ref)?\.os\.writeClipboard", PLUGIN)


def test_plugin_bug_url_base_matches_web():
    assert "github.com/KeyArgo/hermes-revamp/issues/new" in PLUGIN
    assert "github.com/KeyArgo/hermes-revamp/issues/new" in WEB


def test_place_menu_first_run_tip():
    assert "placeMenuTip" in PLUGIN
    assert "LocationRowWithTip" in PLUGIN
    assert "placeMenuTipSeen" in PLUGIN
    assert "place_menu_tip_seen" in PLUGIN


def test_plugin_keeps_private_bounded_browser_diagnostics_and_offers_recovery():
    assert "revamp:diagnostic" in PLUGIN
    assert "diagnostics" in PLUGIN
    assert "Recover player" in PLUGIN
    assert "Copy diagnostics" in PLUGIN
    assert "engine?.reload()" in PLUGIN


def test_diagnostic_timestamps_js_cannot_format_are_dropped():
    # new Date(ts).toISOString() throws past +-8.64e15 ms, which broke the Report bug button.
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "desktop/plugin.js").read_text(encoding="utf-8")
    assert "!(Math.abs(record.ts) <= 8.64e15)) return" in text
    assert "Math.abs(row.ts) <= 8.64e15 ? new Date(row.ts).toISOString() : '?'" in text
