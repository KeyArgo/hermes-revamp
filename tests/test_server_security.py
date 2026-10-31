"""Unauthenticated HTML must only be served to the loopback origin it is for."""


def test_index_rejects_a_rebinding_host_before_embedding_token(server):
    status, _headers, body = server.client.get("/", headers={"Host": "attacker.example"}, raw=True)
    assert status == 421
    assert b"revamp-token" not in body


def test_every_route_rejects_a_rebinding_host_even_with_a_token(server):
    status, _headers, body = server.client.get(
        "/api/state", headers={"Host": "attacker.example", "X-Revamp-Token": server.token}, raw=True)
    assert status == 421
    assert b"volume" not in body


def test_health_and_options_reject_a_rebinding_host(server):
    for method, path in (("GET", "/api/health"), ("OPTIONS", "/api/state")):
        status, _headers, _body = server.client._req(
            method, path, headers={"Host": "attacker.example"}, raw=True)
        assert status == 421


def test_host_must_name_this_listener_port(server):
    status, _headers, _body = server.client.get(
        "/api/health", headers={"Host": "127.0.0.1:1"}, raw=True)
    assert status == 421


def test_both_loopback_names_are_accepted(server):
    port = server.client.base.rsplit(":", 1)[1].rstrip("/")
    for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        status, _headers, _body = server.client.get("/api/health", headers={"Host": host}, raw=True)
        assert status == 200, host


def test_lookalike_hosts_are_rejected(server):
    port = server.client.base.rsplit(":", 1)[1].rstrip("/")
    for host in (f"127.0.0.1.attacker.example:{port}", f"localhost.:{port}", "127.0.0.1", "localhost",
                 f"127.0.0.2:{port}", f"[::1]:{port}", f"0.0.0.0:{port}"):
        status, _headers, body = server.client.get("/", headers={"Host": host}, raw=True)
        assert status == 421, host
        assert b"revamp-token" not in body, host


def test_wrong_token_is_rejected(server):
    status, _headers, _body = server.client.get("/api/state", headers={"X-Revamp-Token": "x" * len(server.token)}, raw=True)
    assert status == 401


def test_radio_proxy_hangs_up_on_a_listener_that_stops_reading(server, monkeypatch):
    # A paused or vanished player stops reading the endless radio stream; the proxy
    # thread used to block on write forever, holding the upstream connection too.
    import socket, threading, time
    from revamp_core import radio, server as srv
    closed = threading.Event()
    class Endless:
        headers = {"Content-Type": "audio/mpeg"}
        def read(self, n): return b"\0" * n
        def close(self): closed.set()
    monkeypatch.setattr(radio, "safe_stream_url", lambda url: True)
    monkeypatch.setattr(radio, "open_stream", lambda url, icy=False: Endless())
    monkeypatch.setattr(srv.Handler, "STREAM_WRITE_TIMEOUT", 0.5)
    port = int(server.client.base.rsplit(":", 1)[1].rstrip("/"))
    s = socket.create_connection(("127.0.0.1", port))
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.sendall(f"GET /api/radio/stream?url=https://station.example/live&t={server.token} HTTP/1.1\r\n"
              f"Host: 127.0.0.1:{port}\r\n\r\n".encode())
    try:
        assert closed.wait(10), "proxy kept writing to a listener that stopped reading"
    finally:
        s.close()


def test_a_stalled_request_is_dropped(server, monkeypatch):
    # Headers that never finish used to hold a server thread forever.
    import socket, time
    from revamp_core import server as srv
    monkeypatch.setattr(srv.Handler, "timeout", 1)
    port = int(server.client.base.rsplit(":", 1)[1].rstrip("/"))
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n".encode())  # no blank line
    s.settimeout(10)
    t = time.time()
    try:
        data = s.recv(1024)
    except socket.timeout:
        data = None
    finally:
        s.close()
    assert data == b"" and time.time() - t < 8, "server kept the stalled connection open"


def _media_server(server, monkeypatch, tmp_path, size=4096):
    from revamp_core import library
    song = tmp_path / "song.mp3"; song.write_bytes(b"\x01" * size)
    monkeypatch.setattr(library, "get_track", lambda tid: {"path": str(song)} if tid == "t1" else None)
    return song, int(server.client.base.rsplit(":", 1)[1].rstrip("/"))


def _read_response(s):
    data = b""
    while b"\r\n\r\n" not in data:
        data += s.recv(4096)
    head, body = data.split(b"\r\n\r\n", 1)
    length = int([l for l in head.split(b"\r\n") if l.lower().startswith(b"content-length:")][0].split(b":")[1])
    while len(body) < length:
        chunk = s.recv(65536)
        if not chunk:
            break
        body += chunk
    return head, body, length


def test_media_keepalive_gets_its_request_deadline_back(server, monkeypatch, tmp_path):
    # Local media lifts the socket deadline so a paused <audio> can idle. On a reused
    # connection that left the next request read with no deadline: half a request held
    # a server thread forever.
    import socket, time
    from revamp_core import server as srv
    monkeypatch.setattr(srv.Handler, "timeout", 1)
    _song, port = _media_server(server, monkeypatch, tmp_path)
    s = socket.create_connection(("127.0.0.1", port)); s.settimeout(10)
    try:
        s.sendall(f"GET /api/media/t1?t={server.token} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        _head, body, length = _read_response(s)
        assert len(body) == length == 4096
        s.sendall(f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n".encode())  # never finished
        t = time.time()
        try:
            data = s.recv(1024)
        except socket.timeout:
            data = None
        assert data == b"" and time.time() - t < 8, "reused media connection kept no request deadline"
    finally:
        s.close()


def test_media_that_shrinks_mid_response_closes_the_connection(server, monkeypatch, tmp_path):
    # Content-Length promised more than the file had: reusing the connection would make
    # the next response look like the missing media bytes.
    import os, socket
    song, port = _media_server(server, monkeypatch, tmp_path)
    real = os.path.getsize
    monkeypatch.setattr(os.path, "getsize", lambda p: real(p) + 1000 if str(p) == str(song) else real(p))
    s = socket.create_connection(("127.0.0.1", port)); s.settimeout(10)
    try:
        s.sendall(f"GET /api/media/t1?t={server.token} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        _head, body, length = _read_response(s)
        assert length == 5096 and len(body) == 4096
        assert s.recv(1024) == b"", "connection stayed open after a short body"
    finally:
        s.close()


def test_non_ascii_token_is_rejected_not_crashed(server):
    for path, headers in (("/api/state?t=%C3%A9%E2%98%83", {"X-Revamp-Token": "x"}), ("/api/state", {"X-Revamp-Token": "été"})):
        status, _headers, _body = server.client.get(path, headers=headers, raw=True)
        assert status == 401, (path, status)


def test_event_stream_drops_a_listener_whose_writes_fail(server):
    # Write errors on events were swallowed: a gone listener (or a half-written frame
    # after the write deadline) kept the thread writing until the next 15 s ping.
    import socket, struct, time
    from revamp_core import server as srv
    port = int(server.client.base.rsplit(":", 1)[1].rstrip("/"))
    before = srv.HUB.count()
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(f"GET /api/events?t={server.token} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    s.settimeout(5); s.recv(1024)
    deadline = time.time() + 5
    while srv.HUB.count() <= before and time.time() < deadline:
        time.sleep(0.05)
    assert srv.HUB.count() == before + 1
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # reset, not FIN
    s.close()
    deadline = time.time() + 6  # well under the 15 s ping
    while srv.HUB.count() > before and time.time() < deadline:
        srv.HUB.broadcast("state", {"x": "y" * 2000})
        time.sleep(0.2)
    assert srv.HUB.count() == before, "event stream kept writing to a dead listener"


def _raw(port, data, wait=2.0):
    import socket, time
    s = socket.create_connection(("127.0.0.1", port)); s.settimeout(0.5)
    s.sendall(data)
    out, end = b"", time.time() + wait
    while time.time() < end:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            break
        out += chunk
    s.close()
    return out


def _port(server):
    return int(server.client.base.rsplit(":", 1)[1].rstrip("/"))


def test_head_is_answered_not_501(server):
    p = _port(server)
    out = _raw(p, f"HEAD /api/health HTTP/1.1\r\nHost: 127.0.0.1:{p}\r\nConnection: close\r\n\r\n".encode())
    assert out.startswith(b"HTTP/1.0 200") or out.startswith(b"HTTP/1.1 200"), out[:60]
    assert b"\r\n\r\n" in out and out.split(b"\r\n\r\n", 1)[1] == b""


def test_a_rejected_requests_body_is_never_run_as_a_request(server):
    # 401 left the body unread; it was then parsed and answered as the next request.
    p = _port(server)
    inner = f"GET /api/state?t={server.token} HTTP/1.1\r\nHost: 127.0.0.1:{p}\r\n\r\n".encode()
    out = _raw(p, f"POST /api/state HTTP/1.1\r\nHost: 127.0.0.1:{p}\r\nContent-Length: {len(inner)}\r\n\r\n".encode() + inner)
    assert out.count(b"HTTP/1.") == 1 and b" 401 " in out.split(b"\r\n")[0], out[:200]


def test_chunked_bodies_are_refused(server):
    p = _port(server)
    out = _raw(p, f"POST /api/command HTTP/1.1\r\nHost: 127.0.0.1:{p}\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n".encode())
    assert b" 411 " in out.split(b"\r\n")[0]


def test_range_edge_cases(server, monkeypatch, tmp_path):
    from revamp_core import library
    empty = tmp_path / "empty.mp3"; empty.write_bytes(b"")
    monkeypatch.setattr(library, "get_track", lambda tid: {"path": str(empty)})
    st, _h, _b = server.client._req("GET", "/api/media/x", headers={"Range": "bytes=-5"}, raw=True)
    assert st == 416
    st, _h, _b = server.client._req("GET", "/api/media/x", headers={"Range": "bytes=" + "9" * 5000 + "-"}, raw=True)
    assert st == 416


def test_odd_paths_and_bodies_are_not_500s(server):
    st, _h, _b = server.client._req("GET", "/web/%00", raw=True)
    assert st == 404
    st, _h, _b = server.client._req("POST", "/api/playlists/import", body={
        "name": "x", "format": "m3u", "text": "#EXTM3U\n/a.mp3\n", "origin_dir": 123}, raw=True)
    assert st != 500


def test_radio_proxy_never_serves_a_runnable_page(server, monkeypatch):
    from revamp_core import radio
    class Html:
        headers = {"Content-Type": "text/html"}
        def __init__(self): self.n = 0
        def read(self, n):
            self.n += 1
            return b"<script>alert(1)</script>" if self.n == 1 else b""
        def close(self): pass
    monkeypatch.setattr(radio, "safe_stream_url", lambda u: True)
    monkeypatch.setattr(radio, "open_stream", lambda u, icy=False: Html())
    st, h, _b = server.client._req("GET", "/api/radio/stream?url=http://x.example/", raw=True)
    assert h.get("X-Content-Type-Options") == "nosniff" and h.get("Content-Security-Policy") == "sandbox"


def test_nowplaying_route_answers_from_cache_without_dns(server, monkeypatch):
    import time
    from revamp_core import radio
    url = "http://station.example/live"
    radio._NP_CACHE[url] = (time.time(), "Cached")
    def dns_down(u):
        raise AssertionError("looked the station up again")
    monkeypatch.setattr(radio, "safe_stream_url", dns_down)
    try:
        st, _h, body = server.client.get("/api/radio/nowplaying?url=" + url)
        assert st == 200 and body == {"title": "Cached"}
    finally:
        radio._NP_CACHE.pop(url, None)
