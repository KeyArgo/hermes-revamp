"""Guard: no API route answers 500 to junk input.

The bug hunts kept finding routes that crashed on a wrong-type field (a number where a
string was expected, a list where an object was, a NUL byte in a path...). Rather than
one test per route, this sends every route a spread of junk and fails on any 500, so a
new route or field that forgets to check its input is caught the day it is written.

The internet and the pop-out browser are switched off for the run: only loopback works.
"""
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "revamp_core" / "server.py").read_text(encoding="utf-8")

EXACT = sorted(set(re.findall(r'path == "(/api/[^"]+)"', SERVER_SRC)))
PREFIXES = sorted(set(re.findall(r'path\.startswith\("(/api/[^"]+)"\)', SERVER_SRC)))
BODY_KEYS = sorted(set(re.findall(r'body\.get\("([A-Za-z_]+)"', SERVER_SRC)) | {"action", "url", "path", "station"})
QUERY_KEYS = sorted(set(re.findall(r'(?:qs\.get\(|_int_qs\(qs, )"([A-Za-z_]+)"', SERVER_SRC)) - {"t"})

JUNK = [None, 0, -1, 10**30, 3.5, True, "", "x" * 3000, "../../../etc/passwd", "\u0000",
        "é☃\U0001F600", [], [1, "a", None], {}, {"a": {"b": [1]}}]
QUERY_JUNK = ["", "-5", "abc", "9" * 40, "../..", "%00", "é☃", "1e999", "[]"]
# Routes whose whole job is to start something outside the test (a browser window).
SKIP = {"/api/window/open", "/api/shutdown-for-upgrade", "/api/events"}  # browser / stops the server / endless stream


@pytest.fixture()
def offline(monkeypatch):
    """Only loopback resolves: every outside fetch fails fast instead of reaching the internet."""
    real = socket.getaddrinfo

    def loopback_only(host, *a, **k):
        if host in ("127.0.0.1", "localhost", "::1"):
            return real(host, *a, **k)
        raise socket.gaierror("network is off in this test")

    monkeypatch.setattr(socket, "getaddrinfo", loopback_only)
    from revamp_core import window
    monkeypatch.setattr(window, "open_window", lambda *a, **k: (_ for _ in ()).throw(window.WindowError("off")))
    monkeypatch.setattr(window, "close_window", lambda *a, **k: False)


def _send(server, method, path, body=None):
    url = server.client.base + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"X-Revamp-Token": server.token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (ConnectionError, urllib.error.URLError) as e:  # dropped without an answer
        return 599


def _paths():
    for p in EXACT:
        if p not in SKIP:
            yield p
    for p in PREFIXES:
        base = p if p.endswith("/") else p + "/"
        for seg in ("x", "..%2F..%2Fetc", "%00", "%C3%A9", "a/b/c", "9" * 30):
            yield base + seg


def test_routes_were_found():
    assert len(EXACT) > 30 and PREFIXES, "the route scan no longer matches server.py"


def test_no_route_answers_500_to_junk(server, offline):
    failures = []
    for path in _paths():
        for method in ("GET", "POST", "PUT", "DELETE"):
            if method in ("GET", "DELETE"):
                for v in QUERY_JUNK:
                    qs = urllib.parse.urlencode({k: v for k in QUERY_KEYS})
                    st = _send(server, method, f"{path}?{qs}")
                    if st >= 500 and st != 502:  # 502: an upstream we deliberately cut off
                        failures.append((method, path, v, st))
            else:
                for v in JUNK:
                    st = _send(server, method, path, {k: v for k in BODY_KEYS})
                    if st >= 500 and st != 502:
                        failures.append((method, path, repr(v)[:30], st))
                for raw in ([1, 2], "str", 5, None):
                    st = _send(server, method, path, raw)
                    if st >= 500 and st != 502:
                        failures.append((method, path, f"body={raw!r}", st))
    # Still alive afterwards.
    assert _send(server, "GET", "/api/health") == 200
    assert not failures, f"{len(failures)} junk requests got a server error, e.g. {failures[:12]}"
