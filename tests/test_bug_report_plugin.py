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


def test_the_page_asks_the_host_to_open_the_issue_form():
    """The page's "Open GitHub issue" called window.open inside a frame sandboxed without popups,
    so it did nothing in Hermes. Embedded, it now posts a URL-less request; the host (which trusts
    only its own frames) copies the details and opens the form."""
    agent = (REPO / "web" / "views" / "agent.js").read_text(encoding="utf-8")
    assert "embeddedInHost()" in agent
    assert 'postMessage({ type: "revamp:open-bug-report" }, "*")' in agent
    handler = PLUGIN[PLUGIN.index("if (fromPage && e.data.type === 'revamp:open-bug-report')"):][:200]
    assert "openBugReport(false)" in handler
    assert "e.data.url" not in handler, "the host must never open a URL a page message names"


def test_the_host_opens_the_short_form_and_copies_the_details():
    open_fn = PLUGIN[PLUGIN.index("async function openBugReport("):]
    open_fn = open_fn[:open_fn.index("\n}\n")]
    assert "writeClipboard(formatBugReportBody(env))" in open_fn
    assert "openExternal(buildBugReportUrl(env))" in open_fn
    build = PLUGIN[PLUGIN.index("function buildBugReportUrl("):]
    build = build[:build.index("\n}\n")]
    assert "'template', 'bug_report.yml'" in build
    assert "'body'" not in build, "the report never rides in the URL (Windows drops long ones)"


def test_the_desktop_chip_reads_the_build_through_the_player_first():
    read = PLUGIN[PLUGIN.index("async function readBuild("):]
    read = read[:read.index("\n}\n")]
    assert read.index("bridge.request('/api/version'") < read.index("ctxRef?.rest('/version'")


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
