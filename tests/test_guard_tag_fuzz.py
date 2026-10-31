"""Guard: no music file, however broken or hostile, crashes or stalls the tag reader.

Library scans read tags from whatever files users have: truncated downloads, half-written
copies, files renamed to the wrong extension, or crafted ones. Every hunt found another
parser that crashed or spun on one of those. This feeds the stdlib parsers (the path every
install has) thousands of seeded mutations of real headers and fails on any exception or
any file that takes too long.
"""
import random
import struct
import time

import pytest

from revamp_core import tags

SEEDS = 1500  # per format; seeded, so a failure reproduces exactly
SLOW = 2.0   # seconds: real files parse in milliseconds


def _box(typ, payload):
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _synch(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _id3():
    frame = b"TIT2" + struct.pack(">I", 6) + b"\x00\x00" + b"\x00Hello"
    apic = b"APIC" + struct.pack(">I", 20) + b"\x00\x00" + b"\x00image/jpeg\x00\x03\x00\xff\xd8\xff\xe0"
    body = frame + apic
    return b"ID3\x03\x00\x00" + _synch(len(body)) + body + b"\xff\xfb\x90\x00" + b"\x00" * 600


def _flac():
    info = struct.pack(">H", 4096) * 2 + b"\x00" * 6 + struct.pack(">Q", (44100 << 44) | (1 << 41) | (15 << 36) | 441000)[:8] + b"\x00" * 16
    block = bytes([0x00]) + len(info).to_bytes(3, "big") + info[:34]
    vc = b"\x05\x00\x00\x00revmp\x01\x00\x00\x00\x0b\x00\x00\x00TITLE=Hello"
    return b"fLaC" + block + bytes([0x84]) + len(vc).to_bytes(3, "big") + vc + b"\x00" * 200


def _ogg():
    head = b"\x01vorbis" + b"\x00" * 4 + b"\x02" + struct.pack("<I", 44100) + b"\x00" * 13
    page = b"OggS\x00\x02" + b"\x00" * 8 + b"\x01\x00\x00\x00" + b"\x00" * 8 + bytes([1, len(head)]) + head
    return page + page.replace(b"\x02", b"\x04", 1) + b"\x00" * 100


def _mp4():
    data = _box(b"data", struct.pack(">I", 1) + b"\x00" * 4 + b"Hello")
    ilst = _box(b"ilst", _box(b"\xa9nam", data) + _box(b"covr", _box(b"data", struct.pack(">I", 13) + b"\x00" * 4 + b"\xff\xd8")))
    mvhd = _box(b"mvhd", b"\x00" * 12 + struct.pack(">II", 1000, 200000) + b"\x00" * 80)
    moov = _box(b"moov", mvhd + _box(b"udta", _box(b"meta", b"\x00" * 4 + ilst)))
    return _box(b"ftyp", b"M4A \x00\x00\x00\x00") + moov


def _wav():
    fmt = struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
    return b"RIFF" + struct.pack("<I", 36 + 400) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 400) + b"\x00" * 400


FORMATS = {".mp3": _id3, ".flac": _flac, ".ogg": _ogg, ".m4a": _mp4, ".wav": _wav}


def _mutate(rng, data):
    b = bytearray(data)
    for _ in range(rng.randint(1, 8)):
        op = rng.randrange(5)
        if op == 0 and b:  # flip bytes
            i = rng.randrange(len(b)); b[i] = rng.randrange(256)
        elif op == 1 and b:  # truncate
            del b[rng.randrange(len(b)):]
        elif op == 2:  # huge or zero length fields
            if len(b) > 8:
                i = rng.randrange(len(b) - 4)
                b[i:i + 4] = rng.choice([b"\xff\xff\xff\xff", b"\x00\x00\x00\x00", b"\x7f\xff\xff\xff", b"\x00\x00\x00\x08"])
        elif op == 3:  # duplicate a slice (repeated boxes/frames)
            if b:
                i = rng.randrange(len(b)); j = min(len(b), i + rng.randint(1, 64))
                b[i:i] = b[i:j] * rng.randint(1, 50)
        else:  # junk tail
            b += bytes(rng.randrange(256) for _ in range(rng.randint(1, 200)))
    return bytes(b)


@pytest.fixture()
def stdlib_only(monkeypatch):
    monkeypatch.setattr(tags, "_HAVE_MUTAGEN", False)
    monkeypatch.setattr(tags, "_ffprobe_available", lambda: False)


@pytest.mark.parametrize("ext", sorted(FORMATS))
def test_broken_files_never_crash_or_stall_the_tag_reader(tmp_path, stdlib_only, ext):
    rng = random.Random(f"revamp-{ext}")
    base = FORMATS[ext]()
    f = tmp_path / f"song{ext}"
    for seed in range(SEEDS):
        data = base if seed == 0 else _mutate(rng, base)
        f.write_bytes(data)
        t = time.monotonic()
        try:
            tags.probe(str(f))
            tags.has_art(str(f))
            tags.read_cover(str(f))
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{ext} seed {seed}: {type(exc).__name__}: {exc}")
        assert time.monotonic() - t < SLOW, f"{ext} seed {seed} took too long"
