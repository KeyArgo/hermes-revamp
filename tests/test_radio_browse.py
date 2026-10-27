"""Endless browse pages, category curation and the new radio routes. All Radio
Browser traffic is faked — no test in here should ever hit the network."""
import pytest

from revamp_core import radio
from revamp_core.radio import RadioError


@pytest.fixture(autouse=True)
def _clean():
    radio._reset_caches()
    yield
    radio._reset_caches()


def _raw_st(name="Good FM", url="https://a.example/stream", **over):
    d = {"stationuuid": name, "name": name, "url": url, "url_resolved": url,
         "homepage": "https://a.example", "tags": "rock, classic", "codec": "MP3",
         "bitrate": "128", "favicon": "", "country": "United States",
         "countrycode": "US", "votes": "500", "clickcount": "1000",
         "lastcheckok": "1"}
    d.update(over)
    return d


# ---------------------------------------------------------------------------
# browse(): exact filters ANDed with a name search, pages, cache
# ---------------------------------------------------------------------------

def test_browse_builds_an_exact_filter_query(monkeypatch):
    seen = {}

    def fake(path, params=None):
        seen["path"], seen["params"] = path, dict(params or {})
        return [_raw_st()], 1234

    monkeypatch.setattr(radio, "_rb_json", fake)
    page = radio.browse(q="jazz", tag="rock", country="de", language="german",
                        sort="votes", limit=50, offset=50)
    p = seen["params"]
    assert seen["path"] == "stations/search"          # ANDs filters with `name`
    assert p["name"] == "jazz"
    assert p["tag"] == "rock" and p["tagexact"] == "true"
    assert p["countrycode"] == "DE" and p["countrycodeexact"] == "true"
    assert p["language"] == "german" and p["languageexact"] == "true"
    assert p["order"] == "votes" and p["reverse"] == "true" and p["hidebroken"] == "true"
    assert p["limit"] == 100 and p["offset"] == 50       # 2x over-fetch window
    assert page["total"] == 1234 and page["more"] is False   # one station on a 100-slot window: end of list
    assert page["stations"][0]["countrycode"] == "US"


def test_browse_sort_falls_back_to_clickcount(monkeypatch):
    seen = {}
    monkeypatch.setattr(radio, "_rb_json",
                        lambda path, params=None: (seen.update(params=params) or ([], None)))
    radio.browse(sort="nonsense", limit=10)
    assert seen["params"]["order"] == "clickcount"


def test_browse_full_window_means_more_short_window_stops(monkeypatch):
    def fake(path, params=None):
        lim = int(params.get("limit", 1))
        return ([_raw_st(name=f"S{i}", url=f"https://s{i}.example/st") for i in range(lim)], None)
    monkeypatch.setattr(radio, "_rb_json", fake)
    page = radio.browse(limit=5)
    assert len(page["stations"]) == 5 and page["more"] is True
    monkeypatch.setattr(radio, "_rb_json", lambda path, params=None: ([_raw_st()], None))
    assert radio.browse(limit=10, offset=100)["more"] is False   # a short window ends the scroll


def test_browse_overfetch_absorbs_filtered_duds(monkeypatch):
    def fake(path, params=None):
        lim = int(params.get("limit", 1))
        raw = [_raw_st(name=f"Dead {i}", url="", url_resolved="") if i % 2
               else _raw_st(name=f"Live {i}", url=f"https://l{i}.example/st")
               for i in range(lim)]
        return raw, None
    monkeypatch.setattr(radio, "_rb_json", fake)
    page = radio.browse(tag="rock", limit=6)
    assert len(page["stations"]) == 6                   # the 2x window filled past the broken rows
    assert [s["name"] for s in page["stations"]] == [f"Live {i}" for i in range(0, 12, 2)]
    assert page["more"] is True


def test_browse_pages_cached_and_returned_as_copies(monkeypatch):
    calls = {"n": 0}

    def fake(path, params=None):
        calls["n"] += 1
        return [_raw_st()], None

    monkeypatch.setattr(radio, "_rb_json", fake)
    p1 = radio.browse(q="x", limit=1)
    p2 = radio.browse(q="x", limit=1)
    assert calls["n"] == 1                            # one page per query, served from cache
    assert p1["stations"] == p2["stations"]
    assert p1["stations"][0] is not p2["stations"][0]  # never a shared dict


# ---------------------------------------------------------------------------
# categories(): hidden junk, merged spellings, sane values
# ---------------------------------------------------------------------------

def _tag(name, count):
    return {"name": name, "stationcount": str(count)}


def test_categories_tags_hide_junk_and_merge_spelling_variants(monkeypatch):
    rows = [_tag("Pop", 500), _tag("Hip Hop", 300), _tag("hip-hop", 100), _tag("HipHop", 50),
            _tag("rock", 400), _tag("Radio", 900), _tag("247", 800), _tag("Música", 700),
            _tag("tiny", 9)]
    monkeypatch.setattr(radio, "_rb_json", lambda path, params=None: (rows, None))
    items = radio.categories("tags")
    assert [i["name"] for i in items] == ["Pop", "Hip Hop", "rock"]   # counts win the order
    assert items[1]["count"] == 450                                    # Hip Hop + hip-hop + HipHop
    assert items[1]["value"] == "Hip Hop"
    names = {i["name"].lower() for i in items}
    assert "radio" not in names and "247" not in names and "música" not in names
    assert all(i["count"] >= radio.MIN_TAG_STATIONS for i in items)


def test_categories_countries_strip_leading_the_and_use_iso_codes(monkeypatch):
    rows = [{"name": "The United States", "iso_3166_1": "US", "stationcount": "1000"},
            {"name": "Liechtenstein", "iso_3166_1": "LI", "stationcount": "2"},
            {"name": "France", "iso_3166_1": "FR", "stationcount": "300"}]
    monkeypatch.setattr(radio, "_rb_json", lambda path, params=None: (rows, None))
    assert radio.categories("countries") == [
        {"name": "United States", "value": "US", "count": 1000},
        {"name": "France", "value": "FR", "count": 300},
    ]


def test_categories_languages_skip_unknown_and_tiny(monkeypatch):
    rows = [{"name": "Spanish", "stationcount": "5000"},
            {"name": "unknown", "stationcount": "9999"},
            {"name": "Other", "stationcount": "9999"},
            {"name": "Klingon", "stationcount": "1"}]
    monkeypatch.setattr(radio, "_rb_json", lambda path, params=None: (rows, None))
    assert radio.categories("languages") == [{"name": "Spanish", "value": "Spanish", "count": 5000}]


def test_categories_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown category kind"):
        radio.categories("zodiac-signs")


def test_rj_json_tolerates_garbage_total_header(monkeypatch):
    class Resp:
        headers = {"x-rbtotal": "nonsense"}

        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(radio.urllib.request, "urlopen", lambda req, timeout=None: Resp())
    assert radio._rb_json("tags") == ([], None)


def test_rb_json_walks_the_mirrors_and_reads_x_rbtotal(monkeypatch):
    urls = []

    class Resp:
        headers = {"x-rbtotal": "42"}

        def read(self):
            return b'[{"url": "https://x/s"}]'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        urls.append(req.full_url)
        assert req.get_header("User-agent") == radio.UA
        if "all.api" in req.full_url:
            raise OSError("wedged round-robin node")   # the real-world failure mode
        return Resp()

    monkeypatch.setattr(radio.urllib.request, "urlopen", fake)
    data, total = radio._rb_json("stations/search", {"limit": 1})
    assert urls[0].startswith("https://all.api.")
    assert any("de2.api." in u for u in urls)          # fell through to a live mirror
    assert data == [{"url": "https://x/s"}] and total == 42


def test_rb_json_raises_when_every_mirror_is_down(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("dns")

    monkeypatch.setattr(radio.urllib.request, "urlopen", boom)
    with pytest.raises(RadioError, match="radio-browser unreachable"):
        radio._rb_json("tags")


# ---------------------------------------------------------------------------
# Server routes (real HTTP against the test server; module fakes apply to it)
# ---------------------------------------------------------------------------

def test_browse_route_shape_and_outage_502(server, monkeypatch):
    monkeypatch.setattr(radio, "_rb_json", lambda path, params=None: ([_raw_st()], 7))
    code, _, page = server.client.get("/api/radio/browse?q=jazz&tag=rock&limit=10&sort=trending")
    assert code == 200
    assert page["total"] == 7 and page["more"] is False
    assert page["stations"][0]["name"] == "Good FM"

    def down(path, params=None):
        raise RadioError("radio-browser unreachable: dns")

    monkeypatch.setattr(radio, "_rb_json", down)
    code, _, body = server.client.get("/api/radio/browse?q=nothingcached")  # fresh query → real try
    assert code == 502 and "unreachable" in body["error"]


def test_categories_route_kind_validation_and_502(server, monkeypatch):
    monkeypatch.setattr(radio, "_rb_json",
                        lambda path, params=None: ([{"name": "Pop", "stationcount": "500"}], None))
    code, _, items = server.client.get("/api/radio/categories?kind=tags")
    assert code == 200 and items == [{"name": "Pop", "value": "Pop", "count": 500}]
    code, _, body = server.client.get("/api/radio/categories?kind=zodiac-signs")
    assert code == 400 and "unknown" in body["error"]


def test_popular_route_stays_a_plain_list(server, monkeypatch):
    monkeypatch.setattr(radio, "_rb_json",
                        lambda path, params=None: ([_raw_st(name=f"P{i}", url=f"https://p{i}/s",
                                                             stationuuid=f"p{i}") for i in range(60)], None))
    code, _, page = server.client.get("/api/radio/popular?limit=15&offset=0")
    assert code == 200 and isinstance(page, list) and len(page) == 15      # desktop/dashboard contract
    code, _, page2 = server.client.get("/api/radio/popular?limit=15&offset=15")
    assert page and page2 and {s["url"] for s in page}.isdisjoint({s["url"] for s in page2})
