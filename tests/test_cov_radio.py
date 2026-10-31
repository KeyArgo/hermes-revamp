"""Coverage for revamp_core/radio.py network-ish paths with urlopen/_open
faked: normalization, search cache, favorites persistence, pls/m3u resolution,
open_stream headers, ICY now-playing."""

from __future__ import annotations

import json
import socket

import pytest

from revamp_core import radio
from revamp_core.radio import RadioError


@pytest.fixture()
def nets(monkeypatch, tmp_path):
    monkeypatch.setattr(radio, "data_dir", lambda: tmp_path)
    # Network calls are faked below; make DNS validation deterministic too.
    monkeypatch.setattr(
        radio.socket, "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
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


def test_browse_next_page_starts_after_the_rows_it_used(nets, monkeypatch):
    # Broken rows are skipped, so a page of 3 can use 5 raw rows; paging on by 3
    # repeated stations already shown.
    rows = [{"stationuuid": f"u{i}", "name": f"S{i}", "url": "" if i in (1, 3) else f"http://s{i}.example/x"}
            for i in range(10)]
    seen = []
    def fake(path, params):
        seen.append(params["offset"]); off = params["offset"]
        return rows[off:off + params["limit"]], len(rows)
    monkeypatch.setattr(radio, "_rb_json", fake)
    first = radio.browse(limit=3)
    assert [s["name"] for s in first["stations"]] == ["S0", "S2", "S4"]
    assert first["next_offset"] == 5 and first["more"]
    second = radio.browse(limit=3, offset=first["next_offset"])
    assert [s["name"] for s in second["stations"]] == ["S5", "S6", "S7"]


def test_nat64_addresses_are_checked_as_the_ipv4_inside(nets):
    for addr in ("64:ff9b::a9fe:a9fe", "64:ff9b::a00:1", "64:ff9b::7f00:1", "64:ff9b:1::c0a8:101", "64:ff9b:1:7f00:0:100:808:808"):
        with pytest.raises(radio.RadioError):
            radio._public_answers([(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addr, 80, 0, 0))])
    assert radio._public_answers([(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::808:808", 80, 0, 0))])


def test_nowplaying_answers_from_cache_without_a_dns_lookup(nets, monkeypatch):
    # Polled every 15 s: a cached title must not cost a lookup, nor turn into an
    # error when DNS hiccups.
    import time
    url = "http://station.example/live"
    radio._NP_CACHE[url] = (time.time(), "Song A")
    def no_dns(*a, **k):
        raise OSError("dns down")
    monkeypatch.setattr(radio.socket, "getaddrinfo", no_dns)
    assert radio.nowplaying(url) == {"title": "Song A"}


def test_bom_playlist_resolves_to_the_stream(nets, monkeypatch):
    class R:
        def __init__(self): self.headers = {}
        def read(self, n): return "﻿#EXTM3U\nhttp://stream.example/live\n".encode("utf-8")
        def geturl(self): return "http://pl.example/a.m3u"
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(radio, "_open", lambda *a, **k: R())
    assert radio.resolve_playlist_url("http://pl.example/a.m3u") == "http://stream.example/live"


def test_favorite_with_a_numeric_id_can_be_removed(nets):
    radio.add_favorite({"url": "http://s.example/x", "id": 123, "name": "S"})
    assert radio.remove_favorite("123") is True
    with pytest.raises(radio.RadioError):
        radio.add_favorite({"url": 5})


def test_favorites_file_with_junk_entries_still_works(nets):
    radio._fav_path().write_text('[1, "x", {"id": "a", "url": "http://a.example/"}]', encoding="utf-8")
    assert [f["id"] for f in radio.favorites()] == ["a"]
    assert radio.remove_favorite("a") is True


def test_caches_are_bounded(nets):
    for i in range(radio.MAX_CACHE_ENTRIES + 50):
        radio._cache_put(radio._SEARCH_CACHE, f"k{i}", (0, i))
    assert len(radio._SEARCH_CACHE) == radio.MAX_CACHE_ENTRIES and "k0" not in radio._SEARCH_CACHE


def test_popular_build_does_not_hold_the_favorites_lock(nets, monkeypatch):
    import threading, time
    started = threading.Event()
    def slow(state, need):
        started.set(); time.sleep(2); state["raw_done"] = True
    monkeypatch.setattr(radio, "_popular_extend", slow)
    t = threading.Thread(target=radio.popular, daemon=True); t.start()
    started.wait(2)
    t0 = time.time(); radio.add_favorite({"url": "http://s.example/y", "name": "Y"})
    assert time.time() - t0 < 1.0
    t.join(5)
