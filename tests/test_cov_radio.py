"""Coverage for revamp_core/radio.py network-ish paths with urlopen/_open
faked: normalization, search cache, favorites persistence, pls/m3u resolution,
open_stream headers, ICY now-playing."""

from __future__ import annotations

import json

import pytest

from revamp_core import radio
from revamp_core.radio import RadioError


@pytest.fixture()
def nets(monkeypatch, tmp_path):
    monkeypatch.setattr(radio, "data_dir", lambda: tmp_path)
    saved = (dict(radio._SEARCH_CACHE), dict(radio._NP_CACHE))
    radio._SEARCH_CACHE.clear()
    radio._NP_CACHE.clear()
    yield tmp_path
    radio._SEARCH_CACHE.clear()
    radio._SEARCH_CACHE.update(saved[0])
    radio._NP_CACHE.clear()
    radio._NP_CACHE.update(saved[1])


class Resp:
    def __init__(self, body=b"", headers=None, final_url=None):
        self.body, self.headers, self._final = body, headers or {}, final_url
        self.pos = 0

    def read(self, n=-1):
        if n is None or n < 0:
            out, self.pos = self.body[self.pos:], len(self.body)
        else:
            out = self.body[self.pos:self.pos + n]
            self.pos += len(out)
        return out

    def geturl(self):
        return self._final or ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# normalization + search
# ---------------------------------------------------------------------------

def _raw(**over):
    base = {"stationuuid": " uuid-1 ", "name": "  ", "url": "http://a/stream",
            "url_resolved": "", "tags": "a, b,, c", "bitrate": "128",
            "codec": "MP3", "favicon": "http://f", "country": "US",
            "homepage": "http://h"}
    base.update(over)
    return base


def test_normalize_station_edges():
    assert radio._normalize_station({"url": "   "}, "radiobrowser") is None   # 222
    assert radio._normalize_station(_raw(hls="1"), "x") is None               # 224
    assert radio._normalize_station(_raw(url="http://a/x.m3u8"), "x") is None
    assert radio._normalize_station(_raw(url="http://a/p.m3u8?token=1"), "x") is None
    st = radio._normalize_station(_raw(), "radiobrowser")
    assert st["id"] == "rb-uuid-1" and st["name"] == "Unnamed"
    assert st["tags"] == ["a", "b", "c"] and st["bitrate"] == 128
    junk = radio._normalize_station(_raw(bitrate="wide"), "radiobrowser")       # 229-230
    assert junk["bitrate"] == 0
    nouuid = radio._normalize_station({"stationuuid": " ", "url": "http://b/s",
                                       "name": "N", "tags": ["," * 200]}, "src")
    assert nouuid["id"].startswith("rb-url-") and nouuid["source"] == "src"


def test_search_cache_params_and_errors(nets, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["ua"] = req.get_header("User-agent")
        return Resp(json.dumps([_raw(), {"url": ""}]).encode("utf-8"))
    monkeypatch.setattr(radio.urllib.request, "urlopen", fake_urlopen)

    out = radio.search_radiobrowser("jazz", "cool", 5)                 # 257+260
    assert [s["name"] for s in out] == ["Unnamed"]                     # url-less dropped
    assert "name=jazz" in seen["url"] and "tag=cool" in seen["url"]
    assert "limit=5" in seen["url"] and "hidebroken=true" in seen["url"]
    assert seen["ua"] == radio.UA

    calls = {"n": 0}
    real = fake_urlopen

    def spy(req, timeout=None):
        calls["n"] += 1
        return real(req, timeout)
    monkeypatch.setattr(radio.urllib.request, "urlopen", spy)
    again = radio.search_radiobrowser("jazz", "cool", 5)               # 254 cache hit
    assert again == out and calls["n"] == 0
    assert again is not out and again[0] is not out[0]                 # copies

    def boom(req, timeout=None):
        raise OSError("dns")
    monkeypatch.setattr(radio.urllib.request, "urlopen", boom)
    with pytest.raises(RadioError, match="unreachable"):               # 266-267
        radio.search_radiobrowser("other", "", 3)


def test_search_non_list_body(nets, monkeypatch):
    monkeypatch.setattr(radio.urllib.request, "urlopen",
                        lambda req, timeout=None: Resp(b'{"oops": 1}'))
    assert radio.search_radiobrowser("x", "", 3) == []


# ---------------------------------------------------------------------------
# favorites
# ---------------------------------------------------------------------------

def test_favorites_roundtrip_errors_and_replace(nets):
    assert radio.favorites() == []                                     # missing file
    (nets / "radio_favorites.json").write_text("{ broken")
    with pytest.raises(RadioError, match="could not read radio favorites"):
        radio.favorites()
    (nets / "radio_favorites.json").write_text('{"not": "a list"}')
    with pytest.raises(RadioError, match="favorites must be a list"):
        radio.favorites()

    (nets / "radio_favorites.json").unlink()

    with pytest.raises(RadioError, match="needs a url"):               # 317
        radio.add_favorite({"name": "no url"})
    st = radio.add_favorite({"name": "Custom", "url": "http://x/s"})
    assert st["source"] == "custom" and st["id"].startswith("custom-")
    # same stream URL under a different id replaces the old entry
    st2 = radio.add_favorite({"id": "keepme", "url": "http://x/s/", "name": "New"})
    favs = radio.favorites()
    assert [f["name"] for f in favs] == ["New"]
    assert radio.remove_favorite("nope") is False                      # 338
    assert radio.remove_favorite("keepme") is True
    assert radio.favorites() == []


def test_save_favorites_base_exception_cleans_tmp(nets):
    with pytest.raises(TypeError):
        radio._save_favorites([{"bad": object()}])
    assert not list(nets.glob(".fav-*.tmp"))
    assert not (nets / "radio_favorites.json").exists()


# ---------------------------------------------------------------------------
# playlist resolve / open_stream / nowplaying
# ---------------------------------------------------------------------------

def test_resolve_playlist_url(nets, monkeypatch):
    assert radio.resolve_playlist_url("http://a/live.mp3") == "http://a/live.mp3"

    monkeypatch.setattr(radio, "_open",
                        lambda u, h, t: Resp(b"[playlist]\nFile1=/streams/s1.mp3\n"))
    assert radio.resolve_playlist_url("http://a/one.pls") == "http://a/streams/s1.mp3"
    monkeypatch.setattr(radio, "_open",
                        lambda u, h, t: Resp(b"#EXTM3U\n#EXTINF:0\n../../rel.mp3\n"))
    assert radio.resolve_playlist_url("http://a/b/x.m3u") == "http://a/rel.mp3"
    monkeypatch.setattr(radio, "_open",
                        lambda u, h, t: (_ for _ in ()).throw(OSError("down")))
    assert radio.resolve_playlist_url("http://a/one.pls") == "http://a/one.pls"  # 361-362
    monkeypatch.setattr(radio, "_open", lambda u, h, t: Resp(b"#nothing\n\n"))
    assert radio.resolve_playlist_url("http://a/one.m3u") == "http://a/one.m3u"  # 372


def test_open_stream_headers(nets, monkeypatch):
    opened = []

    def fake_open(url, headers, timeout):
        opened.append((url, headers, timeout))
        return Resp(b"", final_url=url)
    monkeypatch.setattr(radio, "_open", fake_open)
    resp = radio.open_stream("http://a/live.mp3")                      # 381-389
    assert opened[0][1] == {"User-Agent": radio.UA}
    radio.open_stream("http://a/live.mp3", icy=True)
    assert opened[1][1]["Icy-MetaData"] == "1"
    # redirect to a different final url still returns the response
    monkeypatch.setattr(radio, "_open",
                        lambda u, h, t: Resp(b"", final_url="http://cdn/live.mp3"))
    assert radio.open_stream("http://a/live.mp3") is not None


def _icy_resp(metaint, stream_title=None, icy_name=None):
    headers = {}
    body = b""
    if metaint:
        headers["icy-metaint"] = str(metaint)
        meta = f"StreamTitle='{stream_title}';".encode() if stream_title else b""
        pad = (-len(meta)) % 16
        meta_padded = meta + b"\0" * pad
        blen = len(meta_padded) // 16
        headers["x"] = None
        body = b"x" * metaint + bytes([blen]) + meta_padded
    elif icy_name:
        headers["icy-name"] = icy_name
    headers = {k: v for k, v in headers.items() if v is not None}
    return Resp(body, headers=headers)


def test_nowplaying_paths(nets, monkeypatch):
    r = _icy_resp(16, "Artist - Song")
    monkeypatch.setattr(radio, "_open", lambda u, h, t: r)
    assert radio.nowplaying("http://a/live1") == {"title": "Artist - Song"}
    assert r.headers.get("x") is not None or True

    monkeypatch.setattr(radio, "_open", lambda u, h, t: Resp(b"", final_url=""))
    # second hit served from cache without touching _open again
    hits = []
    monkeypatch.setattr(radio, "_open",
                        lambda u, h, t: hits.append(1) or _icy_resp(16))
    radio.nowplaying("http://a/live1")
    assert hits == []                                                  # 399 cache

    monkeypatch.setattr(radio, "_open",
                        lambda u, h, t: _icy_resp(None, icy_name="Cool FM"))
    assert radio.nowplaying("http://a/live2") == {"title": "Cool FM"}  # 420-421

    def boom(u, h, t):
        raise OSError("stream died")
    monkeypatch.setattr(radio, "_open", boom)
    assert radio.nowplaying("http://a/live3") == {"title": None}       # 422-423
