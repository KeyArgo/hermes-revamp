"""REVamp promises tags and durations without FFmpeg (README: "Tags and durations are read in
Python"). These check that promise with real ID3 tags in every common text encoding, so a machine
without FFmpeg (most Windows installs) still shows titles and artists instead of "Unknown Artist"."""
from __future__ import annotations

import pytest

from conftest import id3_mp3
from revamp_core import tags

TITLE = "Söng Ünï"      # non-ASCII on purpose: encodings are where readers break
ARTIST = "Ärtist – 日本"


@pytest.mark.parametrize("version,encoding,title,artist", [
    (3, 0, "Song One", "The Artists"),   # ID3v2.3, latin-1 (the most common tag on disk)
    (3, 1, TITLE, ARTIST),               # ID3v2.3, UTF-16 with BOM
    (4, 3, TITLE, ARTIST),               # ID3v2.4, UTF-8, syncsafe frame sizes
])
def test_id3_tags_are_read_in_python(tmp_path, version, encoding, title, artist):
    song = tmp_path / "x.mp3"
    song.write_bytes(id3_mp3(title, artist, "Great Album", version=version, encoding=encoding))
    info = tags.probe(str(song))
    assert info["title"] == title
    assert info["artist"] == artist
    assert info["album"] == "Great Album"
    assert info["codec"] == "mp3" and info["duration"] and info["duration"] > 0


def test_the_test_library_has_real_tags_with_or_without_ffmpeg(server):
    """The shared fixture used to write blank files when FFmpeg was missing, so every artist
    became "Unknown Artist" and the library tests failed (or passed vacuously) on such machines."""
    import time
    deadline = time.time() + 20
    names: set = set()
    while time.time() < deadline:
        st, _, artists = server.client.get("/api/library/artists")
        names = {a["name"] for a in artists} if st == 200 else set()
        if "The Artists" in names:
            break
        time.sleep(0.2)
    assert "The Artists" in names, names
