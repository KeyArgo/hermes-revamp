"""Coverage for revamp_core/client.py without a live server: the request
plumbing is exercised against a faked urlopen; tokens/base defaults against
tmp dirs.
"""

from __future__ import annotations

import json
import types
import urllib.error

import pytest

from revamp_core import client as client_mod
from revamp_core.client import RevampClient, RevampUnavailable


class FakeResp:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def capture(monkeypatch):
    box = {}

    def fake_urlopen(req, timeout=None):
        box["req"] = req
        box["timeout"] = timeout
        body = box.get("body", b"")
        exc = box.get("exc")
        if exc:
            raise exc
        return FakeResp(body)
    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
    return box


def test_init_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(client_mod, "setting", lambda k: 9911)
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    (tmp_path / "token").write_text(" tok \n")
    c = RevampClient()
    assert c.base == "http://127.0.0.1:9911" and c.token == "tok"
    c = RevampClient(base="http://h:1/", token="T", timeout=2)
    assert c.base == "http://h:1" and c.token == "T" and c.timeout == 2


def test_default_token_oserror(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(client_mod, "data_dir",
                        lambda: Path("/this/path/does/not/exist"))
    assert client_mod._default_token() == ""


def test_get_params_headers_empty_body(capture):
    c = RevampClient(base="http://srv:1", token="TK")
    out = c.get("/api/x", keep="1", drop=None, other="a b")
    assert out == {}                                     # empty body -> {}
    req = capture["req"]
    assert req.full_url == "http://srv:1/api/x?keep=1&other=a+b"
    assert req.get_method() == "GET"
    assert req.get_header("X-revamp-token") == "TK"
    assert req.get_header("Accept") == "application/json"
    assert req.data is None
    assert capture["timeout"] == 5.0


def test_post_json_and_raw_fallback(capture):
    c = RevampClient(base="http://srv:1", token="TK")
    capture["body"] = json.dumps({"ok": True}).encode()
    assert c.post("/api/go", {"n": 1}) == {"ok": True}
    req = capture["req"]
    assert req.get_method() == "POST"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"n": 1}
    capture["body"] = b"not json \xff"
    assert c.put("/api/go") == {"raw": "not json \ufffd"}
    assert json.loads(capture["req"].data) == {}         # None body -> {}
    capture["body"] = b'"ok"'
    assert c.delete("/api/go", x=2) == "ok"
    assert "x=2" in capture["req"].full_url


def test_urlerror_and_generic_error(capture):
    c = RevampClient(base="http://down:1", token="")
    capture["exc"] = urllib.error.URLError("refused")
    with pytest.raises(RevampUnavailable, match="unreachable"):
        c.get("/api/health")
    capture["exc"] = RuntimeError("boom")
    with pytest.raises(RevampUnavailable, match="failed"):
        c.get("/api/health")


def test_healthy_and_available(monkeypatch):
    c = RevampClient(base="http://x", token="t")
    monkeypatch.setattr(c, "get", lambda p: {"app": "revamp"})
    assert c.healthy() and c.available()
    monkeypatch.setattr(c, "get", lambda p: {})
    assert not c.healthy()
    monkeypatch.setattr(c, "get", lambda p: {"app": "other"})
    assert not c.healthy()

    def boom(p):
        raise RevampUnavailable("down")
    monkeypatch.setattr(c, "get", boom)
    assert not c.healthy() and not c.available()
