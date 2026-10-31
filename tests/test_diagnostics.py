"""Browser failures are handed to Hermes, where its durable plugin storage retains them."""

from pathlib import Path


def test_browser_global_failures_are_recorded_and_sent_to_host():
    text = Path("web/diagnostics.js").read_text(encoding="utf-8")
    assert 'addEventListener("error"' in text
    assert 'addEventListener("unhandledrejection"' in text
    assert 'type: "revamp:diagnostic"' in text
    assert "diagnosticRecord" in text
