"""DNS and redirect boundaries for user-supplied radio stream URLs."""

import socket
import urllib.request

import pytest

from revamp_core import radio


def _answers(*ips):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6,
             "", (ip, 443, 0, 0) if ":" in ip else (ip, 443)) for ip in ips]


def test_stream_url_requires_every_dns_answer_to_be_global(monkeypatch):
    monkeypatch.setattr(radio.socket, "getaddrinfo", lambda *a, **kw: _answers("8.8.8.8", "127.0.0.1"))
    assert not radio.safe_stream_url("https://station.example/live")


def test_stream_url_accepts_public_answer_and_rejects_non_http(monkeypatch):
    monkeypatch.setattr(radio.socket, "getaddrinfo", lambda *a, **kw: _answers("8.8.8.8"))
    assert radio.safe_stream_url("https://station.example/live")
    assert not radio.safe_stream_url("file:///etc/passwd")
    assert not radio.safe_stream_url("http://user:pass@station.example/live")


def test_redirect_target_is_revalidated_before_following(monkeypatch):
    monkeypatch.setattr(radio.socket, "getaddrinfo", lambda host, *a, **kw: _answers("8.8.8.8") if host == "public.example" else _answers("127.0.0.1"))
    with pytest.raises(radio.RadioError, match="non-public"):
        radio._SafeRedirect().redirect_request(urllib.request.Request("https://public.example/live"), None, 302, "Found", {}, "http://private.example/internal")


def test_open_disables_environment_proxies_and_validates_before_network(monkeypatch):
    seen = {}
    class Opener:
        def open(self, req, timeout):
            seen["url"] = req.full_url
            return object()
    def build(*handlers):
        seen["handlers"] = handlers
        return Opener()
    monkeypatch.setattr(radio.socket, "getaddrinfo", lambda *a, **kw: _answers("8.8.8.8"))
    monkeypatch.setattr(radio.urllib.request, "build_opener", build)
    assert radio._open("https://station.example/live", {"User-Agent": "test"}, 1) is not None
    proxy = next(h for h in seen["handlers"] if isinstance(h, urllib.request.ProxyHandler))
    assert proxy.proxies == {}
    assert seen["url"] == "https://station.example/live"


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.20.30.40", "172.16.3.4", "192." "168.1.1",   # loopback + private
    "169.254.169.254",                                       # cloud metadata
    "100.100.100.200", "100.64.0.1",                         # CGNAT / tailnet / Alibaba metadata
    "0.0.0.0", "::1", "fe80::1", "fd00::1",                  # unspecified, v6 loopback/link-local/ULA
    "::ffff:127.0.0.1", "::ffff:169.254.169.254",            # IPv4-mapped IPv6
])
def test_stream_url_refuses_every_non_public_address(monkeypatch, ip):
    monkeypatch.setattr(radio.socket, "getaddrinfo", lambda *a, **kw: _answers(ip))
    assert not radio.safe_stream_url("https://station.example/live"), ip


def test_unresolvable_stream_host_is_refused(monkeypatch):
    def fail(*a, **kw):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(radio.socket, "getaddrinfo", fail)
    assert not radio.safe_stream_url("https://nowhere.invalid/live")


def test_dns_rebinding_between_check_and_connect_is_refused(monkeypatch):
    # The check and the connection used to resolve the name separately: a zero-TTL
    # record could answer public for the check and 127.0.0.1 for the connection,
    # and the proxy would hand a loopback service's response to the caller.
    import http.server, threading
    class Secret(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"internal secret")
        def log_message(self, *a): pass
    inner = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Secret)
    threading.Thread(target=inner.serve_forever, daemon=True).start()
    port = inner.server_address[1]
    real = socket.getaddrinfo
    calls = []
    def rebinding(host, *a, **kw):
        if host != "rebind.example":
            return real(host, *a, **kw)
        calls.append(host)
        ip = "8.8.8.8" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    monkeypatch.setattr(radio.socket, "getaddrinfo", rebinding)
    try:
        with pytest.raises(radio.RadioError, match="non-public"):
            with radio._open(f"http://rebind.example:{port}/live", {"User-Agent": "t"}, 3) as resp:
                pytest.fail(f"connected to loopback: {resp.read()!r}")
    finally:
        inner.shutdown(); inner.server_close()


def test_connection_goes_to_the_address_that_was_checked(monkeypatch):
    monkeypatch.setattr(radio.socket, "getaddrinfo",
                        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))])
    seen = []
    class FakeSock:
        def __init__(self, *a): pass
        def settimeout(self, t): pass
        def connect(self, addr): seen.append(addr)
        def close(self): pass
    monkeypatch.setattr(radio.socket, "socket", FakeSock)
    assert isinstance(radio._pinned_socket("station.example", 80, 5), FakeSock)
    assert seen == [("8.8.8.8", 80)]


class _Resp:
    def __init__(self, headers, body=b"", url="https://station.example/live"):
        self.headers, self._body, self._url, self.reads = headers, body, url, []
    def read(self, n=-1):
        self.reads.append(n)
        if n is None or n < 0:
            out, self._body = self._body, b""
        else:
            out, self._body = self._body[:n], self._body[n:]
        return out
    def geturl(self): return self._url
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_huge_icy_interval_is_refused_without_buffering(monkeypatch):
    # A station could claim icy-metaint: 1000000000 and keep sending: we read that much
    # into memory before looking for the title.
    resp = _Resp({"icy-metaint": "1000000000"}, b"x" * 100)
    monkeypatch.setattr(radio, "safe_stream_url", lambda u: True)
    monkeypatch.setattr(radio, "resolve_playlist_url", lambda u: u)
    monkeypatch.setattr(radio, "_open", lambda *a, **k: resp)
    radio._NP_CACHE.clear() if hasattr(radio, "_NP_CACHE") else None
    assert radio.nowplaying("https://huge.example/live").get("title") is None
    assert all(n is not None and 0 <= n <= 16 * 1024 for n in resp.reads), resp.reads


def test_normal_icy_interval_is_read_in_pieces_and_finds_the_title(monkeypatch):
    meta = b"StreamTitle='Artist - Song';"
    block = meta + b"\0" * (16 - len(meta) % 16 if len(meta) % 16 else 0)
    body = b"a" * 40000 + bytes([len(block) // 16]) + block
    resp = _Resp({"icy-metaint": "40000"}, body)
    monkeypatch.setattr(radio, "safe_stream_url", lambda u: True)
    monkeypatch.setattr(radio, "resolve_playlist_url", lambda u: u)
    monkeypatch.setattr(radio, "_open", lambda *a, **k: resp)
    assert radio.nowplaying("https://ok.example/live")["title"] == "Artist - Song"
    assert max(n for n in resp.reads if n is not None) <= 16 * 1024


def test_relative_playlist_entry_follows_the_redirected_playlist(monkeypatch):
    resp = _Resp({}, b"#EXTM3U\nlive.mp3\n", url="https://cdn.example/radio/list.m3u")
    monkeypatch.setattr(radio, "_open", lambda *a, **k: resp)
    monkeypatch.setattr(radio, "safe_stream_url", lambda u: True)
    assert radio.resolve_playlist_url("https://station.example/list.m3u") == "https://cdn.example/radio/live.mp3"
