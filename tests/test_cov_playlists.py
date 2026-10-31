"""Branch fill-ins for revamp_core/playlists.py: m3u/pls parser edges,
stored-name fallbacks, and the OSError paths around dir/unlink/write."""

from __future__ import annotations

import json
import pathlib

import pytest

from urllib.parse import unquote

from revamp_core import library
from revamp_core import playlists as pl


@pytest.fixture()
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "data_dir", lambda: tmp_path)
    return tmp_path


def test_dir_mkdir_oserror(home, monkeypatch):
    real = pathlib.Path.mkdir

    def boom(self, *a, **k):
        raise OSError("ro")
    monkeypatch.setattr(pathlib.Path, "mkdir", boom)
    assert str(pl.playlists_dir()).endswith("playlists")
    monkeypatch.setattr(pathlib.Path, "mkdir", real)


def test_exact_display_name_wins_over_a_colliding_slug(home):
    """`Rock!` must not silently open the existing `Rock` playlist."""
    first = pl.create_playlist("Rock")
    second = pl.create_playlist("Rock!")
    assert first["id"] != second["id"]
    assert pl.get_playlist("Rock!")["id"] == second["id"]


def test_item_from_line_paths_and_file_urls(home, monkeypatch):
    assert pl.item_from_line("   ") is None                        # 76
    monkeypatch.setattr(library, "track_id_for", lambda p: "tid-1")
    monkeypatch.setattr(library, "get_track",
                        lambda t: {"url": "/api/media/tid-1"} if t == "tid-1" else None)
    for line in ("C:\\Music\\a.mp3", "file:///Music/a.mp3"):       # drive/file URLs
        it = pl.item_from_line(line)
        assert "/api/media/tid-1" in unquote(it["playback"]["url"])
    miss = pl.item_from_line("http://stream.example/live")
    assert miss["kind"] == "stream"
    assert "stream.example/live" in unquote(miss["playback"]["url"])


def test_read_m3u_edges(home, monkeypatch):
    monkeypatch.setattr(library, "get_track", lambda t: None)
    missing = home / "gone.m3u8"
    assert pl._read_m3u(missing) == []                              # 122-123
    p = home / "edge.m3u8"
    p.write_text("\n".join([
        "#EXTM3U",
        "#EXTREVAMP:{ broken json",                                # 130-131
        "#EXTINF:notanumber,Artist - Title",                        # 139-140
        "http://a/stream1",
        "#EXTINF:60,",                                              # empty label
        "/api/media/77",
        "",
        "# comment",
    ]) + "\n")
    items = pl._read_m3u(p)
    assert len(items) == 2
    assert items[0]["duration"] is None and items[0]["artist"] == "Artist"
    assert items[1]["kind"] == "stream"
    assert "/api/media/77" in unquote(items[1]["playback"]["url"])


def test_meta_and_stored_name_fallbacks(home):
    assert pl._meta_from_items([{"duration": "abc"}, {"duration": None},
                                {"duration": 10}]) == (3, 10.0)   # 112-113
    p = home / "my-list.m3u8"
    p.write_text("#EXTM3U\n#EXTREVAMP-NAME:{ broken\n")            # 175-176
    assert pl._stored_name(p) == "My List"
    assert pl._stored_name(home / "nope.m3u8") == "Nope"            # unreadable (175)


def test_read_pls_edges(home):
    assert pl._read_pls(home / "gone.pls") == []                    # 199-200
    p = home / "lame.pls"
    p.write_text("\n".join([
        "[playlist]",
        "garbage without equals",
        "NumberOfEntries=x",                                        # unmatched key
        "File1=",                                                   # -> item None 205-206
        "Title1=ignored",
        "File2=http://b/stream",
        "Title2=Just A Title",                                      # no " - " -> 213
        "Length2=not-a-number",                                     # 217-218
        "File3=http://c/stream",
        "Title3=Artist - Song",
        "Length3=90.5",
    ]) + "\n")
    items = pl._read_pls(p)
    assert ["http://b/stream" in unquote(items[0]["playback"]["url"]),
            "http://c/stream" in unquote(items[1]["playback"]["url"])] == [True, True]
    assert items[0]["title"] == "Just A Title" and items[0]["duration"] is None
    assert items[1]["artist"] == "Artist" and items[1]["duration"] == 90.5


def test_write_m3u_bad_duration(home):
    pl.create_playlist("BadDur", [{"url": "http://s/x", "duration": "junk",
                                   "title": "T"}])
    body = next(home.glob("playlists/*.m3u8")).read_text()
    assert "#EXTINF:-1,T" in body                                   # 229-230
    assert "#EXTREVAMP-NAME:\"BadDur\"" in body


def test_list_skips_vanishing_file(home, monkeypatch):
    pl.create_playlist("Ghost")
    real_get = pl.get_playlist

    def flaky(pid):
        return None
    monkeypatch.setattr(pl, "get_playlist", flaky)                  # 254-255
    assert pl.list_playlists() == []
    monkeypatch.setattr(pl, "get_playlist", real_get)
    assert [x["name"] for x in pl.list_playlists()] == ["Ghost"]


def test_delete_unlink_oserror(home, monkeypatch):
    pl.create_playlist("Stuck")
    real_unlink = pathlib.Path.unlink

    def boom(self, *a, **k):
        if self.suffix == ".m3u8":
            raise OSError("busy")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "unlink", boom)
    assert pl.delete_playlist("Stuck") is False                     # 313-314
    monkeypatch.setattr(pathlib.Path, "unlink", real_unlink)
    assert pl.delete_playlist("Stuck") is True


def test_import_edges(home, monkeypatch):
    assert pl.import_playlist("x", "exe", "junk") is None           # bad fmt
    assert pl.import_playlist("x", "m3u8", "#EXTM3U\n#nothing") is None  # 335-336

    real_unlink = pathlib.Path.unlink

    def boom(self, *a, **k):
        if self.name == ".import-tmp":
            raise OSError("busy")                                   # 333-334 swallow
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "unlink", boom)
    made = pl.import_playlist("PLS!Imp", "pls",
                              "File1=http://i/s\nTitle1=A - B\nLength1=30\n")
    monkeypatch.setattr(pathlib.Path, "unlink", real_unlink)
    assert made and made["name"] == "PLS!Imp"
    assert made["items"][0]["artist"] == "A"


def test_export_bad_durations_and_miss(home, monkeypatch):
    assert pl.export_playlist("does-not-exist", "m3u8") is None     # 344-345
    pl.create_playlist("Dur", [{"url": "http://s/a", "artist": "A", "title": "B"}])
    name, body, ctype = pl.export_playlist("Dur", "pls")
    assert name.endswith(".pls") and ctype == "audio/x-scpls"
    assert "Length1=-1" in body and "NumberOfEntries=1" in body
    name, body, ctype = pl.export_playlist("Dur", "M3U8")
    assert ctype == "audio/x-mpegurl"
    assert "#EXTINF:-1,A - B" in body

    bogus = {"id": "dur", "name": "Dur",
             "items": [{"url": "http://s/a", "duration": "soon",
                        "artist": "A", "title": "B"},
                       {"url": "http://s/b", "duration": [], "title": "C"}]}
    monkeypatch.setattr(pl, "get_playlist", lambda pid: bogus)
    _, body, _ = pl.export_playlist("Dur", "pls")
    assert "Length1=0" in body and "Length2=0" in body              # 353-356
    _, body, _ = pl.export_playlist("Dur", "m3u8")
    assert "#EXTINF:-1,A - B" in body and "#EXTINF:-1,C" in body    # 363-366


def test_exact_id_wins_over_another_playlists_name(home):
    # "Chill" gets id chill; a second playlist named "chill" gets id chill-2. Looking up
    # "chill" used to return whichever came first in the directory, so edits and
    # deletes could hit the wrong playlist.
    a = pl.create_playlist("Chill", [{"kind": "file", "path": "/a.mp3", "title": "A"}])
    b = pl.create_playlist("chill", [{"kind": "file", "path": "/b.mp3", "title": "B"}])
    assert (a["id"], b["id"]) == ("chill", "chill-2")
    assert pl.get_playlist("chill")["id"] == "chill"
    assert pl.get_playlist("chill-2")["id"] == "chill-2"
    assert pl.get_playlist("Chill")["id"] == "chill"


def test_lookup_keeps_the_real_id_on_case_insensitive_disks(home, monkeypatch):
    # macOS and Windows disks match file names case-insensitively: chill.m3u8 "exists" when
    # asked for Chill.m3u8, so get_playlist("Chill") answered with the id "Chill", which no
    # file has. Simulate such a disk here so Linux CI catches it too.
    real_is_file, real_exists = pathlib.Path.is_file, pathlib.Path.exists

    def folded(real):
        def check(self):
            if real(self):
                return True
            return any(c.name.lower() == self.name.lower() for c in self.parent.glob("*")) if self.parent.is_dir() else False
        return check

    pl.create_playlist("Chill", [{"kind": "file", "path": "/a.mp3", "title": "A"}])
    monkeypatch.setattr(pathlib.Path, "is_file", folded(real_is_file))
    monkeypatch.setattr(pathlib.Path, "exists", folded(real_exists))
    assert pl.get_playlist("Chill")["id"] == "chill"
    assert pl.get_playlist("CHILL")["id"] == "chill"
