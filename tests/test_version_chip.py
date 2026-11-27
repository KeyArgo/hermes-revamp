"""The version chip's one line (reviews/VERSION-CHIP.md), byte for byte.

The server hands the browsers `line` from version_stamp.chip_line(), so the JS never rebuilds
it; these tests pin the module and the /api/version contract."""
from revamp_core import version_stamp


def _info(**kw):
    base = {
        "version": "1.0.0",
        "release": "1.0.0",
        "commit": "bfeb6555aaaa",
        "commit_short": "bfeb6555",
        "commit_date": "2026-11-13T10:00:00-07:00",
        "built_at": "",
        "channel": "release",
        "dirty": False,
        "source": "git",
    }
    base.update(kw)
    return base


def test_release_line():
    assert version_stamp.chip_line(_info()) == "v1.0.0 · bfeb6555 · 2026-11-13"


def test_dev_line_with_commit_count():
    line = version_stamp.chip_line(_info(version="1.0.0-dev.342", channel="dev"))
    assert line == "v1.0.0-dev.342 · bfeb6555 · 2026-11-13"


def test_shallow_dev_line():
    line = version_stamp.chip_line(_info(version="1.0.0-dev", channel="dev"))
    assert line == "v1.0.0-dev · bfeb6555 · 2026-11-13"


def test_dirty_line_says_modified():
    line = version_stamp.chip_line(_info(dirty=True))
    assert line == "v1.0.0 · bfeb6555 · 2026-11-13 · modified"


def test_no_commit_drops_that_part():
    line = version_stamp.chip_line(_info(commit="", commit_short=""))
    assert line == "v1.0.0 · 2026-11-13"


def test_no_date_drops_that_part():
    line = version_stamp.chip_line(_info(commit_date=""))
    assert line == "v1.0.0 · bfeb6555"


def test_describe_is_the_chip_line(monkeypatch):
    info = _info(version="1.0.0-dev.342", dirty=True)
    monkeypatch.setattr(version_stamp, "as_dict", lambda: info)
    assert version_stamp.describe() == version_stamp.chip_line(info)


def test_version_route_returns_the_stamp_and_its_line(server):
    code, _, body = server.client.get("/api/version")
    assert code == 200
    for key in ("version", "release", "commit", "commit_short", "commit_date",
                "built_at", "channel", "dirty", "source"):
        assert key in body
    assert body["line"] == version_stamp.chip_line(body)
