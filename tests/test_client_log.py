"""POST /api/client_log: browser failures reach the private log, bounded and safe.

The page forwards window.onerror / unhandledrejection here. These tests pin the
contract: token required, message/stack truncated, a per-server rate limit, and
URL queries (which carry the token) stripped before anything is logged.
"""

from __future__ import annotations

import logging

from revamp_core import server as srv


def _page_errors(caplog):
    return [r for r in caplog.records
            if r.name == "revamp" and r.levelno == logging.WARNING
            and r.getMessage().startswith("page error:")]


def test_client_log_requires_the_token(server):
    code, _, body = server.client.post("/api/client_log", {"message": "boom"},
                                       headers={"X-Revamp-Token": "not-the-token"})
    assert code == 401
    assert body.get("error") == "invalid or missing token"


def test_client_log_empty_report_is_rejected(server):
    srv._client_log_times.clear()
    code, _, _ = server.client.post("/api/client_log", {})
    assert code == 400


def test_client_log_logs_a_warning(server, caplog):
    srv._client_log_times.clear()
    with caplog.at_level(logging.WARNING, logger="revamp"):
        code, _, body = server.client.post("/api/client_log",
                                           {"message": "paint failed", "stack": "at paint (/web/player.js:12:3)",
                                            "placement": "page"})
    assert code == 200 and body == {"ok": True, "logged": True}
    records = _page_errors(caplog)
    assert len(records) == 1
    msg = records[0].getMessage()
    assert msg.startswith("page error: paint failed")
    assert "[page]" in msg
    assert "/web/player.js:12:3" in msg


def test_client_log_truncates_message_and_stack(server, caplog):
    srv._client_log_times.clear()
    with caplog.at_level(logging.WARNING, logger="revamp"):
        server.client.post("/api/client_log", {"message": "x" * 1000})
        server.client.post("/api/client_log", {"message": "boom", "stack": "y" * 3000})
    records = _page_errors(caplog)
    assert len(records) == 2
    first = records[0].getMessage()
    assert first == "page error: " + "x" * srv.CLIENT_LOG_MAX_MESSAGE
    assert "x" * (srv.CLIENT_LOG_MAX_MESSAGE + 1) not in first
    second = records[1].getMessage()
    assert "y" * srv.CLIENT_LOG_MAX_STACK in second
    assert "y" * (srv.CLIENT_LOG_MAX_STACK + 1) not in second


def test_client_log_strips_url_queries_and_tokens(server, caplog):
    srv._client_log_times.clear()
    secret = "SUPERSECRETTOKEN"
    with caplog.at_level(logging.WARNING, logger="revamp"):
        server.client.post("/api/client_log", {
            "message": f"failed http://127.0.0.1:8114/api/art/123?t={secret}&x=1#frag",
            "stack": f"Error: boom\nhttp://127.0.0.1:8114/web/app.js?access_token={secret}",
        })
    msg = _page_errors(caplog)[0].getMessage()
    assert secret not in msg
    assert "?t=" not in msg and "access_token=" not in msg and "x=1" not in msg and "#frag" not in msg
    # The script path stays: it names what failed.
    assert "/api/art/123" in msg
    assert "/web/app.js" in msg


def test_client_log_folds_newlines_so_a_report_cannot_forge_a_line(server, caplog):
    srv._client_log_times.clear()
    with caplog.at_level(logging.WARNING, logger="revamp"):
        server.client.post("/api/client_log", {"message": "first\nrevamp: health rss_mb=0 fake"})
    records = _page_errors(caplog)
    assert len(records) == 1
    assert "\n" not in records[0].getMessage()


def test_client_log_rate_limits_to_twenty_a_minute(server, caplog):
    srv._client_log_times.clear()
    logged = dropped = 0
    with caplog.at_level(logging.WARNING, logger="revamp"):
        for i in range(25):
            code, _, body = server.client.post("/api/client_log", {"message": f"error {i}"})
            assert code == 200 and body["ok"] is True
            if body["logged"]:
                logged += 1
            else:
                dropped += 1
    assert logged == srv.CLIENT_LOG_RATE == 20
    assert dropped == 5
    assert len(_page_errors(caplog)) == 20
    srv._client_log_times.clear()
