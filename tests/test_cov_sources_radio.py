"""Coverage for revamp_core/sources/radio.py: station_item and the
RadioSource browse/search/item branches."""

from __future__ import annotations

import pytest

from revamp_core import radio
from revamp_core.sources import SourceError
from revamp_core.sources import radio as sradio

STATIONS = [
    {"id": "s1", "url": "http://a/stream", "name": "Alpha",
     "tags": ["rock", "classic", "more"], "favicon": "http://fav"},
    {"id": "s2", "url": "http://b/stream", "name": "Beta", "country": "NL"},
    {"id": "s3", "name": "no url"},
]


def test_station_item_variants():
    a = sradio.station_item(STATIONS[0])
    assert a["source"] == "radio" and a["kind"] == "stream"
    assert a["title"] == "Alpha" and a["artist"] == "rock, classic"
    assert a["art"] == "http://fav"
    b = sradio.station_item(STATIONS[1])
    assert b["artist"] == "NL" and not b.get("art")
    assert sradio.station_item(STATIONS[2]) is None


@pytest.fixture()
def fake_radio(monkeypatch):
    monkeypatch.setattr(radio, "presets", lambda: [STATIONS[0]])
    monkeypatch.setattr(radio, "favorites", lambda: [STATIONS[1], STATIONS[2]])
    monkeypatch.setattr(radio, "search_radiobrowser",
                        lambda **kw: [STATIONS[0], STATIONS[1]])
    return sradio.RadioSource()


def test_browse_paths(fake_radio):
    assert fake_radio.browse("", {})["total"] == 1
    assert fake_radio.browse("presets", {})["total"] == 1
    assert fake_radio.browse("favorites", {})["total"] == 1   # url-less dropped
    with pytest.raises(SourceError) as ei:
        fake_radio.browse("nonsense", {})
    assert ei.value.status == 404


def test_search_limits_and_errors(fake_radio, monkeypatch):
    seen = {}

    def spy(q=None, tag="", limit=30):
        seen.update(q=q, tag=tag, limit=limit)
        return [STATIONS[0]]
    monkeypatch.setattr(radio, "search_radiobrowser", spy)
    out = fake_radio.search("jazz", {"limit": "9999"})
    assert out["total"] == 1 and seen == {"q": "jazz", "tag": "", "limit": 100}
    fake_radio.search("x", {"limit": "junk", "tag": "blues"})
    assert seen["limit"] == 30 and seen["tag"] == "blues"

    def boom(**kw):
        raise radio.RadioError("radiobrowser down")
    monkeypatch.setattr(radio, "search_radiobrowser", boom)
    with pytest.raises(SourceError) as ei:
        fake_radio.search("x", {})
    assert ei.value.status == 502 and "down" in str(ei.value)


def test_item_lookup(fake_radio):
    assert fake_radio.item("station:s1")["title"] == "Alpha"
    assert fake_radio.item("station:s2")["title"] == "Beta"      # favorites too
    assert fake_radio.item("station:nope") is None
    assert fake_radio.item("url:deadbeef") is None               # no station id
