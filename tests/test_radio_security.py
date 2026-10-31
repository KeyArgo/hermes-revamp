"""Radio URLs must not turn the localhost proxy into a file/internal-network reader."""


def test_nowplaying_rejects_non_http_urls_before_opening_them(server, monkeypatch):
    from revamp_core import radio

    opened = []
    monkeypatch.setattr(radio, "_open", lambda url, *_args: opened.append(url))
    status, _headers, body = server.client.get("/api/radio/nowplaying?url=file%3A%2F%2F%2Fetc%2Fpasswd")
    assert status == 400
    assert "http" in body["error"]
    assert opened == []


def test_playlist_cannot_switch_a_safe_request_to_a_file_url(monkeypatch):
    from revamp_core import radio

    class Playlist:
        headers = {}

        def read(self, _size):
            return b"#EXTM3U\nhttp://127.0.0.1:8114/private\n"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    calls = []
    monkeypatch.setattr(radio, "_open", lambda url, *_args: calls.append(url) or Playlist())
    assert radio.resolve_playlist_url("https://example.invalid/list.m3u") == "https://example.invalid/list.m3u"
    assert calls == ["https://example.invalid/list.m3u"]


def test_unframed_radio_proxy_response_closes_connection():
    """HTTP/1.1 clients need an EOF when a stream has no content length."""
    from pathlib import Path

    text = Path("revamp_core/server.py").read_text(encoding="utf-8")
    start = text.index("    def _stream_proxy(self, url: str)")
    end = text.index("    def _sse(self)", start)
    proxy = text[start:end]
    assert 'self.send_header("Connection", "close")' in proxy
    assert "self.close_connection = True" in proxy
