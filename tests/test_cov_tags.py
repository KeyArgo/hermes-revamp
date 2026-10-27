"""Branch coverage for revamp_core/tags.py: content types, filename parsing,
ffprobe JSON, and the optional-mutagen paths (fake mutagen injected via
sys.modules; the real env has no mutagen installed).
"""

from __future__ import annotations

import json
import subprocess
import sys
import types

import pytest

from revamp_core import tags


# ---------------------------------------------------------------------------
# fake mutagen plumbing
# ---------------------------------------------------------------------------

class Info:
    def __init__(self, length=12.34, bitrate=128_000, sample_rate=44100,
                 channels=2):
        self.length, self.bitrate = length, bitrate
        self.sample_rate, self.channels = sample_rate, channels


def _info(name, **kw):
    return type(name, (Info,), {})(**kw)


class TagsDict(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


class BadTags:
    def get(self, k, default=None):
        raise RuntimeError("unreadable tags")


class Pic:
    def __init__(self, data=b"JPEGDATA"):
        self.data = data


class EasyFile:
    """Easy-tag style file: tag values live on attributes *or* .tags."""

    def __init__(self, attrs=None, tags_dict=None, info=None):
        for k, v in (attrs or {}).items():
            setattr(self, k, v)
        self.tags = tags_dict
        self.info = info or Info()


class RawFile:
    def __init__(self, pictures=None, tags=None):
        self.pictures = pictures
        self.tags = tags


def _install_mutagen(monkeypatch, files=None, raises=False):
    mod = types.ModuleType("mutagen")

    def File(path, easy=False):
        if raises:
            raise OSError("corrupt file")
        return (files or {}).get(str(path))
    mod.File = File
    monkeypatch.setattr(tags, "mutagen", mod, raising=False)
    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", True)


# ---------------------------------------------------------------------------
# content types + filename heuristics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,want", [
    ("song.mp3", "audio/mpeg"),
    ("song.FLAC", "audio/flac"),
    ("notes.txt", "text/plain"),
    ("mystery.qqqt", "application/octet-stream"),
])
def test_content_type_for(name, want):
    assert tags.content_type_for(name) == want


@pytest.mark.parametrize("stem,title,artist,no", [
    ("The Artists - Great Song", "Great Song", "The Artists", None),
    ("The Artists - 07 Great Song", "Great Song", "The Artists", 7),
    ("07. Great Song", "Great Song", "", 7),
    ("07-Great Song", "07-Great Song", "", None),
    ("plain title", "plain title", "", None),
    ("1234 Song", "1234 Song", "", None),          # >3 digits: not a track no
    ("A - ", "A - ", "", None),                    # empty right side: no split
])
def test_humanise(stem, title, artist, no):
    assert tags._humanise(stem) == (title, artist, no)


def test_int_or_none():
    assert tags._int_or_none(" 42 ") == 42
    assert tags._int_or_none(None) is None
    assert tags._int_or_none("x") is None


# ---------------------------------------------------------------------------
# ffprobe
# ---------------------------------------------------------------------------

class FakeRun:
    def __init__(self, stdout=None, exc=None):
        self.stdout, self.exc = stdout, exc

    def __call__(self, cmd, **kw):
        assert "ffprobe" in cmd and str(cmd[-1]) == "/x.flac" or True
        if self.exc:
            raise self.exc
        return types.SimpleNamespace(stdout=self.stdout)


def test_probe_ffprobe_full(monkeypatch):
    payload = {"format": {"duration": "120.5", "bit_rate": "192000",
                          "tags": {"Title": "T", "artist": "A", "ALBUM": "B",
                                   "album_artist": "AA", "track": "3/12",
                                   "disc": "1", "date": "1975-06-01",
                                   "genre": "Rock"}},
               "streams": [{"codec_type": "video"},
                           {"codec_type": "audio", "sample_rate": "44100",
                            "channels": 2, "codec_name": "flac"}]}
    monkeypatch.setattr(tags.subprocess, "run", FakeRun(json.dumps(payload).encode()))
    out = tags._probe_ffprobe("/x.flac")
    assert out["duration"] == 120.5 and out["bitrate_kbps"] == 192
    assert out["sample_rate"] == 44100 and out["channels"] == 2
    assert out["codec"] == "flac"
    assert (out["title"], out["artist"], out["album"], out["album_artist"]) == \
        ("T", "A", "B", "AA")
    assert out["track_no"] == 3 and out["disc_no"] == 1
    assert out["year"] == 1975 and out["genre"] == "Rock"


def test_probe_ffprobe_fallbacks_and_failures(monkeypatch):
    # audio duration when format lacks it; codec from format_name; zeros -> None
    payload = {"format": {"tags": {}, "bit_rate": "0"},
               "streams": [{"codec_type": "audio", "duration": "9.123",
                            "sample_rate": "0", "channels": "0"}],
               "format_name": "mp3,mp2"}
    monkeypatch.setattr(tags.subprocess, "run",
                        FakeRun(json.dumps(payload).encode()))
    out = tags._probe_ffprobe("/x.mp3")
    assert out["duration"] == 9.123 and out["bitrate_kbps"] is None
    assert out["sample_rate"] is None and out["channels"] is None
    # duration that will not parse -> the except-pass branch
    payload["format"] = {"duration": "n/a", "tags": {}}
    monkeypatch.setattr(tags.subprocess, "run",
                        FakeRun(json.dumps(payload).encode()))
    assert tags._probe_ffprobe("/x.mp3")["duration"] is None
    # empty stdout -> {}
    monkeypatch.setattr(tags.subprocess, "run", FakeRun(b""))
    assert tags._probe_ffprobe("/x.mp3") == {"duration": None, "bitrate_kbps": None,
                                             "sample_rate": None, "channels": None,
                                             "codec": None, "title": None,
                                             "artist": None, "album": None,
                                             "album_artist": None, "track_no": None,
                                             "disc_no": None, "year": None,
                                             "genre": None}
    # OSError -> {}
    monkeypatch.setattr(tags.subprocess, "run", FakeRun(exc=OSError("no ffprobe")))
    assert tags._probe_ffprobe("/x.mp3") == {}


# ---------------------------------------------------------------------------
# mutagen (faked)
# ---------------------------------------------------------------------------

def test_tags_mutagen_disabled_then_errors(monkeypatch):
    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", False)
    assert tags._tags_mutagen("/x.mp3") == {}
    _install_mutagen(monkeypatch, raises=True)
    assert tags._tags_mutagen("/x.mp3") == {}
    _install_mutagen(monkeypatch, files={})   # File -> None
    assert tags._tags_mutagen("/x.mp3") == {}


def test_tags_mutagen_easy_attrs(monkeypatch):
    f = EasyFile(attrs={"title": ["T"], "artist": "A", "album": ["B", "ignored"],
                        "albumartist": ["AA"], "tracknumber": ["3"],
                        "discnumber": ["1"], "date": ["1975"], "genre": ["Rock"]},
                 tags_dict=None,
                 info=_info("MP3Info"))
    _install_mutagen(monkeypatch, files={"/x": f})
    out = tags._tags_mutagen("/x")
    assert out["title"] == "T" and out["artist"] == "A" and out["album"] == "B"
    assert out["album_artist"] == "AA" and out["genre"] == "Rock"
    assert out["track_no"] == 3 and out["disc_no"] == 1 and out["year"] == 1975
    assert out["duration"] == 12.34 and out["bitrate_kbps"] == 128
    assert out["sample_rate"] == 44100 and out["channels"] == 2
    assert out["codec"] == "mp3"


def test_tags_mutagen_tags_dict_and_empty_lists(monkeypatch):
    f = EasyFile(tags_dict=TagsDict({"album artist": ["AA2"], "title": [],
                                     "year": ["2001"]}),
                 info=_info("FLACInfo"))
    _install_mutagen(monkeypatch, files={"/y": f})
    out = tags._tags_mutagen("/y")
    assert out["title"] is None and out["album_artist"] == "AA2"
    assert out["year"] == 2001 and out["codec"] == "flac"


def test_tags_mutagen_exploding_tags(monkeypatch):
    f = EasyFile(tags_dict=BadTags(), info=Info(bitrate=0, length=0.0))
    _install_mutagen(monkeypatch, files={"/z": f})
    out = tags._tags_mutagen("/z")
    assert out["title"] is None and out["duration"] is None
    assert out["bitrate_kbps"] is None


def test_has_art_paths(tmp_path, monkeypatch):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", False)
    assert tags.has_art(str(song)) is False
    (tmp_path / "front.png").write_bytes(b"p")
    assert tags.has_art(str(song)) is True
    (tmp_path / "front.png").unlink()
    _install_mutagen(monkeypatch, files={str(song): RawFile(pictures=[Pic()])})
    assert tags.has_art(str(song)) is True
    _install_mutagen(monkeypatch, files={
        str(song): RawFile(tags=TagsDict({"covr": [b"img"]}))})
    assert tags.has_art(str(song)) is True
    _install_mutagen(monkeypatch, files={str(song): RawFile(tags=BadTags())})
    assert tags.has_art(str(song)) is False           # tag read errors, no files
    _install_mutagen(monkeypatch, raises=True)
    assert tags.has_art(str(song)) is False


def test_read_cover_paths(tmp_path, monkeypatch):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", False)
    assert tags.read_cover(str(song)) is None
    (tmp_path / "cover.jpg").write_bytes(b"JPEG")
    assert tags.read_cover(str(song)) == b"JPEG"
    _install_mutagen(monkeypatch, files={str(song): RawFile(pictures=[Pic(b"EMB")])})
    assert tags.read_cover(str(song)) == b"EMB"       # embedded wins
    _install_mutagen(monkeypatch, files={
        str(song): RawFile(pictures=[], tags=TagsDict({"metadata": [Pic(b"MD")]}))})
    assert tags.read_cover(str(song)) == b"MD"
    _install_mutagen(monkeypatch, files={
        str(song): RawFile(pictures=[], tags=TagsDict({"covr": b"RAW"}))})
    assert tags.read_cover(str(song)) == b"RAW"
    _install_mutagen(monkeypatch, files={
        str(song): RawFile(pictures=[], tags=BadTags())})
    assert tags.read_cover(str(song)) == b"JPEG"      # falls back to the file
    _install_mutagen(monkeypatch, raises=True)
    assert tags.read_cover(str(song)) == b"JPEG"
    (tmp_path / "cover.jpg").unlink()
    assert tags.read_cover(str(song)) is None


def test_read_cover_unreadable_file(tmp_path, monkeypatch):
    song = tmp_path / "s.mp3"
    song.write_bytes(b"x")
    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", False)
    cover = tmp_path / "folder.jpeg"
    cover.write_bytes(b"z")

    def boom(self):
        raise OSError("no perms")
    monkeypatch.setattr(type(cover), "read_bytes", boom, raising=False)
    assert tags.read_cover(str(song)) is None


# ---------------------------------------------------------------------------
# probe orchestration
# ---------------------------------------------------------------------------

def test_probe_prefers_mutagen_skips_ffprobe(monkeypatch):
    called = []
    monkeypatch.setattr(tags, "_tags_mutagen",
                        lambda p: {"title": "from-mutagen"})
    monkeypatch.setattr(tags, "_probe_ffprobe",
                        lambda p: called.append(p) or {"title": "no"})
    meta = tags.probe("/some/dir/Song Title.mp3")
    assert meta["title"] == "from-mutagen"
    assert called == []                    # ffprobe never touched
    assert meta["album"] == "dir" and meta["track_no"] is None


def test_probe_falls_back_to_ffprobe_then_filename(monkeypatch):
    monkeypatch.setattr(tags, "_tags_mutagen", lambda p: {})
    monkeypatch.setattr(tags, "_probe_ffprobe",
                        lambda p: {"duration": 3.2})
    meta = tags.probe("/lib/The Artist - 4 Deep Cut.mp3")
    assert meta["duration"] == 3.2
    assert meta["title"] == "Deep Cut" and meta["artist"] == "The Artist"
    assert meta["track_no"] == 4


def test_probe_empty_parent_album(monkeypatch):
    monkeypatch.setattr(tags, "_tags_mutagen", lambda p: {})
    monkeypatch.setattr(tags, "_probe_ffprobe", lambda p: {})
    meta = tags.probe("song.mp3")          # parent dir is "" -> no album guess
    assert meta["album"] is None
    assert meta["title"] == "song"
