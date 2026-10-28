"""Stdlib tag and duration parsers, used when ffprobe is missing or returns nothing.

Fixtures are built in Python (minimal MP3 / FLAC / M4A / Ogg / WAV). A second
check generates real files with ffmpeg when it is installed and skips a format
whose encoder is missing.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from revamp_core import library, tags

REPO = Path(__file__).resolve().parent.parent


def _silence(monkeypatch):
    monkeypatch.setattr(tags, "_tags_mutagen", lambda _p: {})
    monkeypatch.setattr(tags, "_probe_ffprobe", lambda _p: {})
    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", False)


def _close(got, expected):
    assert got is not None, "duration missing"
    assert abs(got - expected) < 0.5, (got, expected)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def _synchsafe(n: int) -> bytes:
    out = bytearray(4)
    for i in range(3, -1, -1):
        out[i] = n & 0x7F
        n >>= 7
    return bytes(out)


def _id3_text(fid: bytes, text: str, encoding: str = "utf-8", version: int = 4) -> bytes:
    if encoding == "utf-8":
        payload = bytes([3]) + text.encode("utf-8")
    elif encoding == "latin-1":
        payload = bytes([0]) + text.encode("latin-1")
    elif encoding == "utf-16-le":
        payload = bytes([1]) + b"\xff\xfe" + text.encode("utf-16-le")
    elif encoding == "utf-16-be":
        payload = bytes([1]) + b"\xfe\xff" + text.encode("utf-16-be")
    else:
        raise ValueError(encoding)
    size = _synchsafe(len(payload)) if version >= 4 else len(payload).to_bytes(4, "big")
    return fid + size + b"\x00\x00" + payload


def _id3_apic(image: bytes, version: int = 4) -> bytes:
    payload = bytes([0]) + b"image/jpeg\x00" + bytes([3]) + b"\x00" + image
    size = _synchsafe(len(payload)) if version >= 4 else len(payload).to_bytes(4, "big")
    return b"APIC" + size + b"\x00\x00" + payload


def _id3_tag(frames: list, version: int = 4) -> bytes:
    body = b"".join(frames)
    return b"ID3" + bytes([version, 0, 0]) + _synchsafe(len(body)) + body


def _id3v1(title: str, artist: str, album: str, year: int, track: int) -> bytes:
    def field(text: str, n: int) -> bytes:
        return text.encode("latin-1")[:n].ljust(n, b"\x00")
    comment = b"\x00" * 28 + bytes([0, track])
    return b"TAG" + field(title, 30) + field(artist, 30) + field(album, 30) + field(str(year), 4) + comment + bytes([255])


def _mpeg1_frame(xing_frames=None, vbri_frames=None) -> bytes:
    """128 kbps, 48 kHz, MPEG1 Layer III, 384-byte frame (divides evenly)."""
    frame = bytearray(b"\xff\xfb\x94\x00" + b"\x00" * 380)
    if xing_frames is not None:
        frame[36:40] = b"Xing"
        frame[40:44] = (1).to_bytes(4, "big")
        frame[44:48] = int(xing_frames).to_bytes(4, "big")
    if vbri_frames is not None:
        frame[36:40] = b"VBRI"
        frame[40:42] = (1).to_bytes(2, "big")
        frame[50:54] = int(vbri_frames).to_bytes(4, "big")
    return bytes(frame)


def _mpeg2_xing_frame(frames: int) -> bytes:
    """MPEG2 Layer III, 64 kbps, 22050 Hz. Xing sits after the 17-byte side info."""
    frame_len = 72 * 64000 // 22050
    frame = bytearray(b"\xff\xf3\x80\x00" + b"\x00" * (frame_len - 4))
    at = 4 + 17
    frame[at:at + 4] = b"Xing"
    frame[at + 4:at + 8] = (1).to_bytes(4, "big")
    frame[at + 8:at + 12] = int(frames).to_bytes(4, "big")
    return bytes(frame)


def _flac(rate: int, samples: int, comments: dict, picture: bytes = b"") -> bytes:
    word = (rate << 44) | (1 << 41) | (15 << 36) | samples
    streaminfo = struct.pack(">HH", 16, 16) + b"\x00\x00\x00" + b"\x00\x00\x00"
    streaminfo += word.to_bytes(8, "big") + b"\x00" * 16
    assert len(streaminfo) == 34
    vendor = b"revamp"
    comment = struct.pack("<I", len(vendor)) + vendor
    items = []
    for key, val in comments.items():
        raw = f"{key}={val}".encode("utf-8")
        items.append(struct.pack("<I", len(raw)) + raw)
    comment += struct.pack("<I", len(items)) + b"".join(items)
    blocks = [(0, streaminfo), (4, comment)]
    if picture:
        mime = b"image/jpeg"
        pic = struct.pack(">I", 3) + struct.pack(">I", len(mime)) + mime
        pic += struct.pack(">I", 0) + struct.pack(">IIII", 1, 1, 24, 0)
        pic += struct.pack(">I", len(picture)) + picture
        blocks.append((6, pic))
    out = bytearray(b"fLaC")
    for i, (kind, data) in enumerate(blocks):
        last = 0x80 if i == len(blocks) - 1 else 0
        out += bytes([last | kind]) + len(data).to_bytes(3, "big") + data
    return bytes(out)


def _box(typ: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + typ + payload


def _data_atom(payload: bytes, flags: int = 1) -> bytes:
    return _box(b"data", flags.to_bytes(4, "big") + b"\x00\x00\x00\x00" + payload)


def _m4a(scale: int, duration: int, tags: list, cover: bytes = b"") -> bytes:
    parts = b""
    for typ, value in tags:
        if typ in (b"trkn", b"disk"):
            blob = b"\x00\x00" + int(value).to_bytes(2, "big") + b"\x00\x00" + b"\x00\x00"
            parts += _box(typ, _data_atom(blob, 0))
        else:
            parts += _box(typ, _data_atom(value.encode("utf-8"), 1))
    if cover:
        parts += _box(b"covr", _data_atom(cover, 13))
    ilst = _box(b"ilst", parts)
    meta = _box(b"meta", b"\x00\x00\x00\x00" + ilst)
    mvhd = b"\x00" * 12 + scale.to_bytes(4, "big") + duration.to_bytes(4, "big") + b"\x00" * 80
    moov = _box(b"moov", _box(b"mvhd", mvhd) + _box(b"udta", meta))
    ftyp = _box(b"ftyp", b"M4A \x00\x00\x02\x00M4A mp42isom")
    # Declared huge, not actually written: the walker must seek, not read it.
    mdat = (8 + 50_000_000).to_bytes(4, "big") + b"mdat"
    return ftyp + moov + mdat


def _m4b_chapter_name_then_title(scale: int, duration: int, chapter: str, title: str) -> bytes:
    """Chapter-track ©nam ahead of the movie title at moov/udta/meta/ilst.

    The trak name is stored twice, the way QuickTime leaves it: a direct
    trak/udta/©nam and a trak/udta/meta/ilst/©nam. mdhd disagrees with mvhd
    so a walker that keeps the track duration is visible.
    """
    def named(text: str) -> bytes:
        return _box(b"\xa9nam", _data_atom(text.encode("utf-8")))

    chapter_ilst = _box(b"ilst", named(chapter))
    chapter_meta = _box(b"meta", b"\x00\x00\x00\x00" + chapter_ilst)
    chapter_udta = _box(b"udta", chapter_meta + named(chapter))
    mdhd = b"\x00" * 12 + (1000).to_bytes(4, "big") + (1000).to_bytes(4, "big")
    trak = _box(b"trak", _box(b"mdia", _box(b"mdhd", mdhd)) + chapter_udta)
    ilst = _box(b"ilst", named(title))
    meta = _box(b"meta", b"\x00\x00\x00\x00" + ilst)
    mvhd = b"\x00" * 12 + scale.to_bytes(4, "big") + duration.to_bytes(4, "big") + b"\x00" * 80
    moov = _box(b"moov", _box(b"mvhd", mvhd) + trak + _box(b"udta", meta))
    ftyp = _box(b"ftyp", b"M4B \x00\x00\x00\x00M4B mp42isom")
    return ftyp + moov


def _ogg_page(packets: list, granule: int, bos: bool = False, eos: bool = False, seq: int = 0) -> bytes:
    segs = []
    body = b""
    for packet in packets:
        body += packet
        left = len(packet)
        if left == 0:
            segs.append(0)
            continue
        while left >= 255:
            segs.append(255)
            left -= 255
        segs.append(left)
    flag = (0x02 if bos else 0) | (0x04 if eos else 0)
    page = b"OggS" + bytes([0, flag])
    page += struct.pack("<qIII", granule, 1, seq, 0)
    page += bytes([len(segs)]) + bytes(segs) + body
    return page


def _comment_blob(comments: dict) -> bytes:
    vendor = b"revamp"
    body = struct.pack("<I", len(vendor)) + vendor
    items = []
    for key, val in comments.items():
        raw = f"{key}={val}".encode("utf-8")
        items.append(struct.pack("<I", len(raw)) + raw)
    return body + struct.pack("<I", len(items)) + b"".join(items)


def _vorbis_ogg(rate: int, granule: int, comments: dict) -> bytes:
    ident = bytes([1]) + b"vorbis" + struct.pack("<I", 0) + bytes([2]) + struct.pack("<I", rate)
    ident += struct.pack("<iii", 0, 128000, 0) + bytes([1])
    comment = bytes([3]) + b"vorbis" + _comment_blob(comments) + bytes([1])
    return _ogg_page([ident, comment], 0, bos=True, seq=0) + _ogg_page([b""], granule, eos=True, seq=1)


def _opus_ogg(preskip: int, granule: int, comments: dict) -> bytes:
    ident = b"OpusHead" + bytes([1, 2]) + struct.pack("<H", preskip) + struct.pack("<I", 48000)
    ident += struct.pack("<h", 0) + bytes([0])
    comment = b"OpusTags" + _comment_blob(comments)
    return _ogg_page([ident, comment], 0, bos=True, seq=0) + _ogg_page([b"x"], granule, eos=True, seq=1)


def _wav(rate: int, channels: int, bits: int, seconds: float) -> tuple:
    byte_rate = rate * channels * bits // 8
    data_bytes = int(byte_rate * seconds)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, byte_rate, channels * bits // 8, bits)
    chunks = b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", data_bytes)
    body = b"WAVE" + chunks
    return b"RIFF" + struct.pack("<I", len(body)) + body, data_bytes / byte_rate


def _write(tmp_path: Path, name: str, blob: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(blob)
    return str(path)


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------

def test_mp3_id3_utf8_xing_and_apic(tmp_path, monkeypatch):
    _silence(monkeypatch)
    image = b"\xff\xd8\xff\xd9"
    tag = _id3_tag([
        _id3_text(b"TIT2", "水槽"),
        _id3_text(b"TPE1", "ヨルシカ"),
        _id3_text(b"TALB", "創作"),
        _id3_text(b"TPE2", "ヨルシカ"),
        _id3_text(b"TRCK", "6/12"),
        _id3_text(b"TPOS", "1/2"),
        _id3_text(b"TCON", "J-Pop"),
        _id3_text(b"TDRC", "2023-04-01"),
        _id3_apic(image),
    ])
    path = _write(tmp_path, "song.mp3", tag + _mpeg1_frame(xing_frames=100))
    meta = tags.probe(path)
    assert meta["title"] == "水槽"
    assert meta["artist"] == "ヨルシカ"
    assert meta["album"] == "創作"
    assert meta["album_artist"] == "ヨルシカ"
    assert meta["track_no"] == 6 and meta["disc_no"] == 1
    assert meta["genre"] == "J-Pop" and meta["year"] == 2023
    _close(meta["duration"], 100 * 1152 / 48000)
    assert tags.has_art(path) is True
    assert tags.read_cover(path) == image


def test_mp3_utf16_both_endians_and_latin1(tmp_path, monkeypatch):
    _silence(monkeypatch)
    for encoding, title in (("utf-16-le", "呪縛"), ("utf-16-be", "モノクロの世界"), ("latin-1", "Café")):
        tag = _id3_tag([
            _id3_text(b"TIT2", title, encoding, version=3),
            _id3_text(b"TPE1", "Artist", "latin-1", version=3),
            _id3_text(b"TYER", "1998", "latin-1", version=3),
        ], version=3)
        path = _write(tmp_path, f"{encoding}.mp3", tag + _mpeg1_frame(xing_frames=10))
        meta = tags._probe_stdlib(path)
        assert meta["title"] == title
        assert meta["artist"] == "Artist"
        assert meta["year"] == 1998


def test_mp3_cbr_estimate_and_id3v1(tmp_path, monkeypatch):
    _silence(monkeypatch)
    frames = _mpeg1_frame() * 50
    path = _write(tmp_path, "cbr.mp3", frames + _id3v1("Old Title", "Old Artist", "Old Album", 1984, 4))
    meta = tags._probe_stdlib(path)
    assert (meta["title"], meta["artist"], meta["album"]) == ("Old Title", "Old Artist", "Old Album")
    assert meta["year"] == 1984 and meta["track_no"] == 4
    _close(meta["duration"], 50 * 1152 / 48000)


def test_mp3_v2_wins_over_v1_and_vbri_and_mpeg2(tmp_path, monkeypatch):
    _silence(monkeypatch)
    tag = _id3_tag([_id3_text(b"TIT2", "New")])
    path = _write(tmp_path, "vbri.mp3", tag + _mpeg1_frame(vbri_frames=25) + _id3v1("Old", "A", "B", 2001, 1))
    meta = tags._probe_stdlib(path)
    assert meta["title"] == "New"
    _close(meta["duration"], 25 * 1152 / 48000)
    path = _write(tmp_path, "mpeg2.mp3", _mpeg2_xing_frame(80))
    meta = tags._probe_stdlib(path)
    _close(meta["duration"], 80 * 576 / 22050)


def test_flac_comments_duration_and_picture(tmp_path, monkeypatch):
    _silence(monkeypatch)
    image = b"\xff\xd8jpeg"
    path = _write(tmp_path, "a.flac", _flac(44100, 44100 * 3, {
        "TITLE": "水槽", "ARTIST": "ヨルシカ", "ALBUM": "創作",
        "ALBUMARTIST": "ヨルシカ", "TRACKNUMBER": "3/8", "DATE": "2020-01-02",
        "GENRE": "Pop",
    }, picture=image))
    meta = tags.probe(path)
    assert meta["title"] == "水槽" and meta["artist"] == "ヨルシカ" and meta["album"] == "創作"
    assert meta["album_artist"] == "ヨルシカ" and meta["track_no"] == 3
    assert meta["year"] == 2020 and meta["genre"] == "Pop"
    _close(meta["duration"], 3.0)
    assert tags.has_art(path) is True
    assert tags.read_cover(path) == image


def test_m4a_ilst_mvhd_and_cover(tmp_path, monkeypatch):
    _silence(monkeypatch)
    image = b"\x89PNG\r\n\x1a\n"
    path = _write(tmp_path, "a.m4a", _m4a(44100, 110250, [
        (b"\xa9nam", "水槽"),
        (b"\xa9ART", "ヨルシカ"),
        (b"\xa9alb", "創作"),
        (b"aART", "ヨルシカ"),
        (b"trkn", 6),
        (b"\xa9day", "2019-05-05"),
        (b"\xa9gen", "Pop"),
    ], cover=image))
    meta = tags.probe(path)
    assert meta["title"] == "水槽" and meta["artist"] == "ヨルシカ" and meta["album"] == "創作"
    assert meta["album_artist"] == "ヨルシカ" and meta["track_no"] == 6
    assert meta["year"] == 2019 and meta["genre"] == "Pop"
    _close(meta["duration"], 2.5)
    assert tags.read_cover(path) == image
    # m4b uses the same atoms
    path = _write(tmp_path, "book.m4b", _m4a(1000, 90_000, [(b"\xa9nam", "Chaptered")]))
    meta = tags._probe_stdlib(path)
    assert meta["title"] == "Chaptered"
    _close(meta["duration"], 90.0)


def test_mp4_title_is_file_level_ilst_not_trak_chapter(tmp_path):
    """A chapter-track ©nam must not hide moov/udta/meta/ilst, and mvhd still wins."""
    path = _write(tmp_path, "book.m4b", _m4b_chapter_name_then_title(1000, 90_000, "Chapter 1", "Real Title"))
    meta = tags._probe_stdlib(path)
    assert meta["title"] == "Real Title"
    _close(meta["duration"], 90.0)


def test_ogg_vorbis_and_opus(tmp_path, monkeypatch):
    _silence(monkeypatch)
    comments = {
        "TITLE": "水槽", "ARTIST": "ヨルシカ", "ALBUM": "創作",
        "TRACKNUMBER": "4/9", "DATE": "2021", "GENRE": "Pop",
    }
    path = _write(tmp_path, "a.ogg", _vorbis_ogg(44100, 44100 * 5, comments))
    meta = tags.probe(path)
    assert meta["title"] == "水槽" and meta["artist"] == "ヨルシカ" and meta["album"] == "創作"
    assert meta["track_no"] == 4 and meta["year"] == 2021 and meta["genre"] == "Pop"
    _close(meta["duration"], 5.0)
    preskip = 312
    path = _write(tmp_path, "a.opus", _opus_ogg(preskip, 48000 * 2 + preskip, comments))
    meta = tags._probe_stdlib(path)
    assert meta["title"] == "水槽" and meta["codec"] == "opus"
    _close(meta["duration"], 2.0)


def test_wav_duration_from_header_not_payload(tmp_path, monkeypatch):
    _silence(monkeypatch)
    blob, seconds = _wav(44100, 2, 16, 3.5)
    assert len(blob) < 100  # the sample bytes were not written
    path = _write(tmp_path, "a.wav", blob)
    meta = tags._probe_stdlib(path)
    _close(meta["duration"], seconds)
    assert meta["sample_rate"] == 44100 and meta["channels"] == 2


def test_garbage_and_truncated_do_not_raise(tmp_path, monkeypatch):
    _silence(monkeypatch)
    for name, blob in (
        ("zero.mp3", b"\x00" * 64),
        ("rand.flac", os.urandom(40)),
        ("short.ogg", b"OggS"),
        ("short.m4a", b"\x00\x00\x00\x08ftyp"),
        ("short.wav", b"RIFFxxxxWAVE"),
        ("empty.opus", b""),
    ):
        path = _write(tmp_path, name, blob)
        meta = tags.probe(path)
        assert meta["title"]
        assert "duration" in meta


def test_ffprobe_result_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(tags, "_tags_mutagen", lambda _p: {})
    monkeypatch.setattr(tags, "_probe_ffprobe", lambda _p: {"title": "From Probe", "duration": 9})
    path = _write(tmp_path, "a.mp3", _mpeg1_frame(xing_frames=10))
    meta = tags.probe(path)
    assert meta["title"] == "From Probe" and meta["duration"] == 9


def test_player_reports_duration_it_learns():
    text = (REPO / "web" / "player.js").read_text(encoding="utf-8")
    assert "/api/library/duration" in text
    assert "loadedmetadata" in text
    assert "reportLearnedDuration" in text


# ---------------------------------------------------------------------------
# learned duration: library + token-protected route
# ---------------------------------------------------------------------------

def test_learned_duration_survives_rescan(tmp_path, monkeypatch):
    home = tmp_path / "data"
    home.mkdir()
    music = tmp_path / "music"
    music.mkdir()
    (music / "No Tags.mp3").write_bytes(b"\x00" * 32)
    monkeypatch.setenv("REVAMP_HOME", str(home))
    monkeypatch.setattr(library, "_INDEX", {})
    monkeypatch.setattr(library, "_LAST_SCAN", 0.0)
    _silence(monkeypatch)
    library.scan_sync([str(music)])
    track = library.all_tracks()[0]
    assert track["duration"] is None
    assert library.set_learned_duration(track["id"], 95.25) == 95.25
    assert library.set_learned_duration(track["id"], 95.4) == 95.25  # within 0.5 s
    assert library.set_learned_duration(track["id"], True) is None
    assert library.set_learned_duration(track["id"], 0) is None
    library.scan_sync([str(music)])
    assert library.get_track(track["id"])["duration"] == 95.25


def test_duration_endpoint_is_token_protected(server):
    _code, _, body = server.client.get("/api/library/tracks?limit=5")
    track = body["tracks"][0]
    tid = track["id"]
    library._INDEX[tid]["duration"] = None
    code, _, out = server.client.post("/api/library/duration", {"id": tid, "duration": 183.5})
    assert code == 200, out
    assert out["duration"] == 183.5
    listed = server.client.get("/api/library/tracks?limit=50")[2]["tracks"]
    assert next(t for t in listed if t["id"] == tid)["duration"] == 183.5
    # local item id, the shape the player sends for core items
    code, _, out = server.client.post("/api/library/duration",
                                      {"id": "local:track:" + tid, "duration": 200})
    assert code == 200 and out["duration"] == 200
    assert server.client.post("/api/library/duration", {"duration": 10})[0] == 400
    assert server.client.post("/api/library/duration", {"id": tid, "duration": "long"})[0] == 400
    assert server.client.post("/api/library/duration", {"id": "missing", "duration": 10})[0] == 404
    code, _, _ = server.client._req(
        "POST", "/api/library/duration", body={"id": tid, "duration": 12},
        headers={"X-Revamp-Token": "not-the-token"})
    assert code == 401


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ffmpeg_files_when_present(tmp_path, monkeypatch):
    _silence(monkeypatch)
    ffmpeg = shutil.which("ffmpeg")
    specs = [
        ("a.mp3", ["-c:a", "libmp3lame", "-b:a", "128k"], True),
        ("a.flac", ["-c:a", "flac"], True),
        ("a.ogg", ["-c:a", "libvorbis", "-q:a", "2"], True),
        ("a.opus", ["-c:a", "libopus", "-b:a", "64k"], True),
        ("a.m4a", ["-c:a", "aac", "-b:a", "128k"], True),
        ("a.wav", ["-c:a", "pcm_s16le"], False),
    ]
    checked = 0
    for name, enc, expect_tags in specs:
        out = tmp_path / name
        cmd = [ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
               "sine=frequency=440:duration=2",
               "-metadata", "title=水槽", "-metadata", "artist=ヨルシカ",
               "-metadata", "album=創作", *enc, str(out)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=40)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            continue
        meta = tags._probe_stdlib(str(out))
        _close(meta.get("duration"), 2.0)
        if expect_tags:
            assert meta.get("title") == "水槽", (name, meta)
            assert meta.get("artist") == "ヨルシカ", (name, meta)
            assert meta.get("album") == "創作", (name, meta)
        checked += 1
    assert checked >= 1
