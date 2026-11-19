"""HTTP framing regressions found during the release hunt."""

import socket


def _exchange(server, request):
    port = int(server.client.base.rsplit(':', 1)[1].rstrip('/'))
    with socket.create_connection(('127.0.0.1', port), timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall(request(port))
        data = b''
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                return data
            data += chunk


def test_invalid_content_length_closes_connection(server):
    for header in ('-1', '+1', '1\r\nContent-Length: 0'):
        out = _exchange(server, lambda p: (
            f'GET /api/health HTTP/1.1\r\nHost: localhost:{p}\r\n'
            f'Content-Length: {header}\r\n\r\n'
            f'GET /api/health HTTP/1.1\r\nHost: localhost:{p}\r\n\r\n').encode())
        assert b' 400 ' in out.split(b'\r\n', 1)[0], (header, out)
        assert out.count(b'HTTP/1.1 ') == 1


def test_options_body_is_not_processed_as_another_request(server):
    def request(port):
        inner = f'GET /api/health HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n'.encode()
        return (f'OPTIONS /api/state HTTP/1.1\r\nHost: localhost:{port}\r\n'
                f'Content-Length: {len(inner)}\r\n\r\n').encode() + inner

    out = _exchange(server, request)
    assert b' 204 ' in out.split(b'\r\n', 1)[0]
    assert out.count(b'HTTP/1.1 ') == 1
