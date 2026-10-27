"""Synced lyrics from LRCLIB: parsing, fetching, caching, the route. No network: urlopen is faked."""

from __future__ import annotations

import io
import importlib
import json
import sys
import time
import types
import urllib.error
import urllib.parse

import pytest

from revamp_core import lyrics


def test_parse_lrc_basic_and_edge_cases():
    text = "[ar:Someone]\n[ti:Song]\n[00:01.50]Hello\n[00:03.250]World\n[00:05]\n[00:07.00][00:09.00]Twice\nnot a line\n"
    out = lyrics.parse_lrc(text)
    assert out == [{"t": 1.5, "line": "Hello"}, {"t": 3.25, "line": "World"}, {"t": 5.0, "line": ""},
                   {"t": 7.0, "line": "Twice"}, {"t": 9.0, "line": "Twice"}]


def test_parse_lrc_offset_and_sorting():
    out = lyrics.parse_lrc("[offset:+500]\n[00:02.00]B\n[00:00.20]A\n")
    assert out == [{"t": 0.0, "line": "A"}, {"t": 1.5, "line": "B"}]


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def fake_net(monkeypatch, tmp_path):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    calls = []
    answers = {}

    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        ua = req.get_header("User-agent") if hasattr(req, "get_header") else None
        calls.append((url, ua, timeout))
        for key, val in answers.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return _Resp(json.dumps(val).encode())
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(lyrics.urllib.request, "urlopen", urlopen)
    for name in ("_CACHE", "_MEM"):
        if isinstance(getattr(lyrics, name, None), dict):
            getattr(lyrics, name).clear()
    if hasattr(lyrics, "_loaded"):
        monkeypatch.setattr(lyrics, "_loaded", False, raising=False)
    return calls, answers


def test_fetch_hit_params_and_user_agent(fake_net):
    calls, answers = fake_net
    answers["track_name=Clocks"] = {"syncedLyrics": "[00:01.00]Lights go out", "plainLyrics": "Lights go out",
                                    "instrumental": False}
    out = lyrics.lyrics_for("Coldplay", "Clocks", "A Rush of Blood", 307.4)
    assert out["found"] is True and out["synced"] == [{"t": 1.0, "line": "Lights go out"}]
    assert out["plain"] == "Lights go out" and out["source"] == "lrclib"
    url, ua, timeout = calls[0]
    parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert url.startswith("https://lrclib.net/api/get?")
    assert parsed == {
        "artist_name": ["Coldplay"], "track_name": ["Clocks"],
        "album_name": ["A Rush of Blood"], "duration": ["307"],
    }
    assert ua == "Revamp (Hermes plugin; https://github.com/KeyArgo/hermes-revamp)"
    assert timeout == 8


def test_fetch_omits_optional_empty_params_and_raises_for_non_404(fake_net):
    calls, answers = fake_net
    answers["track_name=Fine"] = {"plainLyrics": "fine"}
    assert lyrics._fetch({"artist_name": "A B", "track_name": "Fine",
                          "album_name": "", "duration": None}) == {"plainLyrics": "fine"}
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[-1][0]).query)
    assert query == {"artist_name": ["A B"], "track_name": ["Fine"]}

    answers["track_name=Broken"] = urllib.error.HTTPError(
        "https://lrclib.net/api/get", 500, "bad", {}, io.BytesIO(b"{}"))
    with pytest.raises(lyrics.LyricsError, match="500"):
        lyrics._fetch({"artist_name": "A", "track_name": "Broken"})


def test_404_retries_with_clean_title_then_caches_the_miss(fake_net, monkeypatch):
    calls, _ = fake_net
    probes = []

    def fetch(params):
        probes.append(params)
        return None

    monkeypatch.setattr(lyrics, "_fetch", fetch)
    out = lyrics.lyrics_for("Band", "Song (Remastered 2011) feat. Guest")
    assert out == {"found": False, "synced": [], "plain": "", "instrumental": False,
                   "source": "lrclib"}
    assert [p["track_name"] for p in probes] == ["Song (Remastered 2011) feat. Guest", "Song"]
    n = len(probes)
    lyrics.lyrics_for("Band", "Song (Remastered 2011) feat. Guest")
    assert len(probes) == n
    assert calls == []


def test_clean_title_can_turn_a_404_into_a_hit(fake_net, monkeypatch):
    probes = []

    def fetch(params):
        probes.append(params["track_name"])
        return None if len(probes) == 1 else {"syncedLyrics": "[00:01]yes", "plainLyrics": "yes"}

    monkeypatch.setattr(lyrics, "_fetch", fetch)
    assert lyrics.lyrics_for("Band", "Song - Remastered 2011") == {
        "found": True, "synced": [{"t": 1.0, "line": "yes"}], "plain": "yes",
        "instrumental": False, "source": "lrclib",
    }
    assert probes == ["Song - Remastered 2011", "Song"]


def test_errors_not_cached(fake_net):
    calls, answers = fake_net
    answers["track_name=Flaky"] = urllib.error.URLError("timed out")
    out = lyrics.lyrics_for("X", "Flaky")
    assert out["found"] is False and "error" in out
    answers["track_name=Flaky"] = {"syncedLyrics": "[00:00.50]ok", "plainLyrics": "ok", "instrumental": False}
    assert lyrics.lyrics_for("X", "Flaky")["found"] is True


def test_missing_fields_no_request(fake_net):
    calls, _ = fake_net
    assert lyrics.lyrics_for("", "Title")["found"] is False
    assert lyrics.lyrics_for("Artist", "")["found"] is False
    assert calls == []


def test_cache_persists_to_disk(fake_net, tmp_path):
    calls, answers = fake_net
    answers["track_name=Keep"] = {"syncedLyrics": "[00:02.00]stay", "plainLyrics": "stay", "instrumental": False}
    lyrics.lyrics_for("A", "Keep")
    assert (tmp_path / "lyrics-cache.json").exists()
    for name in ("_CACHE", "_MEM"):
        if isinstance(getattr(lyrics, name, None), dict):
            getattr(lyrics, name).clear()
    if hasattr(lyrics, "_loaded"):
        lyrics._loaded = False
    n = len(calls)
    assert lyrics.lyrics_for("A", "Keep")["found"] is True and len(calls) == n


def test_normalized_key_and_rounded_duration_share_a_cache_entry(fake_net):
    calls, answers = fake_net
    answers["track_name=Song"] = {"plainLyrics": "cached"}
    assert lyrics.lyrics_for("  ARTIST ", " Song ", " Album ", 9.6)["found"] is True
    assert lyrics.lyrics_for("artist", "song", "album", 10.0)["plain"] == "cached"
    assert len(calls) == 1


def test_disk_cache_is_capped_at_5000_entries(fake_net, monkeypatch):
    # Seed directly so the cap is checked without thousands of file writes.
    monkeypatch.setattr(lyrics, "_save", lambda: None)
    lyrics._CACHE.update({
        json.dumps(("artist", f"old-{n}", "", 0)): {"hit": True, "ts": n, "data": {}}
        for n in range(lyrics.MAX_ENTRIES)
    })
    lyrics._cache_put(("artist", "new", "", 0), {"plain": "new"}, hit=True)
    assert len(lyrics._CACHE) == lyrics.MAX_ENTRIES
    assert json.dumps(("artist", "new", "", 0)) in lyrics._CACHE


def test_route(server, monkeypatch):
    monkeypatch.setattr(lyrics, "lyrics_for", lambda artist, title, album="", duration=None:
                        {"found": True, "synced": [], "plain": f"{artist}/{title}", "instrumental": False,
                         "source": "lrclib"})
    deadline = time.time() + 10
    while time.time() < deadline:
        _, _, body = server.client.get("/api/library/tracks?limit=1")
        if body.get("tracks"):
            break
        time.sleep(0.2)
    tid = body["tracks"][0]["id"]
    code, _, out = server.client.get(f"/api/library/lyrics?id={tid}")
    assert code == 200 and out["id"] == tid and out["found"] is True
    assert server.client.get("/api/library/lyrics?id=nope")[0] == 404
    assert server.client.get("/api/library/lyrics")[0] == 400


def test_proxy_forwards_a_url_encoded_id(monkeypatch):
    class Response:
        def __init__(self, content="", media_type=None, status_code=200):
            self.body = content.encode() if isinstance(content, str) else content
            self.status_code = status_code

    class Router:
        def _deco(self, *args, **kwargs):
            return lambda fn: fn

        get = post = put = delete = _deco

    # FastAPI is optional in the core test environment; handlers themselves
    # are regular functions, so a tiny import stub is sufficient here.
    fake_fastapi = types.ModuleType("fastapi")
    fake_fastapi.APIRouter = Router
    fake_fastapi.Response = Response
    monkeypatch.setitem(sys.modules, "fastapi", fake_fastapi)
    sys.modules.pop("dashboard.plugin_api", None)
    api = importlib.import_module("dashboard.plugin_api")
    seen = []
    monkeypatch.setattr(api, "_request", lambda method, path, body=None, timeout=5.0:
                        (seen.append((method, path)), (200, {"ok": True}))[1])
    response = api.library_lyrics(id="track / & ?")
    assert response.status_code == 200
    assert seen == [("GET", "/api/library/lyrics?id=track+%2F+%26+%3F")]


# ---- from the Codex review round 1

def test_cache_hit_keeps_source(fake_net):
    calls, answers = fake_net
    answers["track_name=Again"] = {"syncedLyrics": "[00:01.00]x", "plainLyrics": "x", "instrumental": False}
    lyrics.lyrics_for("A", "Again")
    second = lyrics.lyrics_for("A", "Again")
    assert second["found"] is True and second["source"] == "lrclib"


def test_instrumental_is_a_hit(fake_net):
    calls, answers = fake_net
    answers["track_name=Interlude"] = {"syncedLyrics": "", "plainLyrics": "", "instrumental": True}
    out = lyrics.lyrics_for("A", "Interlude")
    assert out["found"] is True and out["instrumental"] is True and out["synced"] == []


def test_fetch_404_returns_none(fake_net):
    assert lyrics._fetch({"artist_name": "Nobody", "track_name": "Nothing"}) is None


def test_offset_applies_to_lines_before_it():
    assert lyrics.parse_lrc("[00:02.00]line\n[offset:+500]\n") == [{"t": 1.5, "line": "line"}]


# ---- from the Codex review round 2

def test_cached_miss_expires_from_memory_too(fake_net, monkeypatch):
    calls, answers = fake_net
    lyrics.lyrics_for("Old", "Miss")
    n = len(calls)
    real = time.time()
    monkeypatch.setattr(lyrics.time, "time", lambda: real + 8 * 86400)  # 8 days later
    answers["track_name=Miss"] = {"syncedLyrics": "[00:01.00]now", "plainLyrics": "now", "instrumental": False}
    out = lyrics.lyrics_for("Old", "Miss")
    assert len(calls) > n and out["found"] is True, "an expired miss must be retried"


def test_non_object_json_is_not_a_crash(fake_net):
    calls, answers = fake_net
    answers["track_name=Weird"] = []
    out = lyrics.lyrics_for("X", "Weird")
    assert out["found"] is False
