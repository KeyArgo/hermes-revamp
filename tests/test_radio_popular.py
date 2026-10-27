"""Popular stations: a lazily-built, quality-ranked Radio Browser pool blended
with the curated presets, paged endlessly, with stale/preset fallbacks.
No test here touches the real network."""
import pytest

from revamp_core import radio


@pytest.fixture(autouse=True)
def _clean():
    radio._reset_caches()
    yield
    radio._reset_caches()


def _st(name="Good FM", url="https://a.example/stream", **over):
    d = {"stationuuid": name, "name": name, "url": url, "url_resolved": url,
         "homepage": "https://a.example", "tags": "rock", "codec": "MP3",
         "bitrate": "128", "favicon": "", "country": "United States",
         "countrycode": "US", "votes": "500", "clickcount": "2000",
         "lastcheckok": "1"}
    d.update(over)
    return d


def _good(i, **over):
    ccs = ["US", "DE", "FR", "GB", "IT", "ES", "NL", "BR"]
    over.setdefault("countrycode", ccs[i % len(ccs)])
    return _st(f"Radio Number {i}", f"https://s{i}.example/stream", stationuuid=f"g{i}", **over)


def test_popular_opens_with_featured_presets_then_ranked_radio_browser(monkeypatch):
    monkeypatch.setattr(radio, "_rb_json",
                        lambda path, params=None: ([_good(i) for i in range(12)], None))
    out = radio.popular(limit=30)
    assert [s["id"] for s in out[:10]] == radio.FEATURED_PRESETS
    rb = [s for s in out if s["source"] == "radiobrowser"]
    assert len(rb) == 12
    assert out[10]["url"] == "https://s0.example/stream"  # ranked block sits right after presets
    assert all((s["bitrate"] or 0) >= radio.POPULAR_MIN_BITRATE for s in rb)


def test_preset_clones_are_deduped(monkeypatch):
    rows = [
        _good(1),
        _st("SomaFM Groove Salad (128k MP3)", url="https://copycat/stream", stationuuid="cc1"),
        _st("SomaFM Groove Salad", url="https://other.example/stream",
            homepage="https://evil.example", stationuuid="cc2"),
    ]
    monkeypatch.setattr(radio, "_rb_json", lambda path, params=None: (rows, None))
    urls = [s["url"] for s in radio.popular(limit=60)]
    assert "https://s1.example/stream" in urls
    assert "https://copycat/stream" not in urls       # same name once codec noise is stripped
    assert "https://other.example/stream" not in urls  # same normalized name, different homepage


def test_spam_and_vote_stuffed_listings_are_dropped(monkeypatch):
    rows = [
        _st("Best radio ever", url="https://ok/stream", stationuuid="ok"),
        _st("test", url="https://t/stream", stationuuid="t"),
        _st("Radio Radio", url="https://rr/stream", stationuuid="rr"),
        _st("🎵🎶 Music", url="https://e/stream", stationuuid="e"),
        _st("BEST MUSIC EVER NONSTOP HITS NONSTOP DANCE ANTHEMS FOREVER",
            url="https://caps/stream", stationuuid="caps"),
        _st("a|b|c|d", url="https://pipes/stream", stationuuid="pipes"),
        _st("Vote Stuffed FM", url="https://vs/stream", votes="999999", clickcount="3", stationuuid="vs"),
        _st("Low Bitrate", url="https://lb/stream", bitrate="32", stationuuid="lb"),
        _st("Http Broken", url="http://hb/stream", lastcheckok="0", stationuuid="hb"),
        _st("Http But Verified", url="http://hv/stream", lastcheckok="1", stationuuid="hv"),
    ]
    monkeypatch.setattr(radio, "_rb_json", lambda path, params=None: (rows, None))
    urls = {s["url"] for s in radio.popular(limit=30)}
    assert "https://ok/stream" in urls          # https + sane numbers survives
    assert "http://hv/stream" in urls           # plain http is fine when recently verified working
    for gone in ("https://t/stream", "https://rr/stream", "https://e/stream",
                 "https://caps/stream", "https://pipes/stream", "https://vs/stream",
                 "https://lb/stream", "http://hb/stream"):
        assert gone not in urls


def test_per_country_cap(monkeypatch):
    rows = [_st(f"US One {i}", f"https://us{i}.example/stream", stationuuid=f"u{i}")
            for i in range(radio.POPULAR_MAX_PER_COUNTRY + 3)]
    monkeypatch.setattr(radio, "_rb_json", lambda path, params=None: (rows, None))
    rb = [s for s in radio.popular(limit=80) if s["source"] == "radiobrowser"]
    assert len(rb) == radio.POPULAR_MAX_PER_COUNTRY


def test_popular_pages_without_overlap_and_fetches_the_pool_once(monkeypatch):
    calls = {"n": 0}

    def fake(path, params=None):
        calls["n"] += 1
        ccs = ["US", "DE", "FR", "GB", "IT", "ES", "NL", "BR"] * 5  # exactly 5/country: the cap
        return ([_good(i, countrycode=cc)
                 for i, cc in enumerate(ccs)], None)

    monkeypatch.setattr(radio, "_rb_json", fake)
    p1 = radio.popular(limit=20, offset=0)
    p2 = radio.popular(limit=20, offset=20)
    p3 = radio.popular(limit=20, offset=40)
    assert len(p1) == len(p2) == len(p3) == 20
    urls = [s["url"] for s in p1 + p2 + p3]
    assert len(urls) == len(set(urls))                    # endless scroll never repeats
    assert calls["n"] == 1                                # one window builds the whole pool
    big = radio.popular(limit=20, offset=10000)
    assert big == []                                      # past the end is empty, not an error


def test_popular_falls_back_to_presets_when_offline(monkeypatch):
    def down(path, params=None):
        raise radio.RadioError("radio-browser unreachable: dns")

    monkeypatch.setattr(radio, "_rb_json", down)
    assert radio.popular() == radio.presets()             # never an empty error page
    assert radio.popular(offset=10, limit=5) == []


def test_popular_prefers_the_stale_pool_over_presets(monkeypatch):
    monkeypatch.setattr(radio, "_rb_json",
                        lambda path, params=None: ([_good(9)], None))
    built = radio.popular(limit=60)
    assert any(s["url"] == "https://s9.example/stream" for s in built)
    stale_state = radio._POPULAR
    monkeypatch.setattr(radio, "_rb_json",
                        lambda path, params=None: (_ for _ in ()).throw(
                            radio.RadioError("radio-browser unreachable: down")))
    radio._POPULAR, radio._POPULAR_STALE = None, stale_state
    urls = [s["url"] for s in radio.popular(limit=60)]
    assert "https://s9.example/stream" in urls            # outage keeps the last good pool


def test_popular_returns_copies(monkeypatch):
    monkeypatch.setattr(radio, "_rb_json",
                        lambda path, params=None: ([_good(1)], None))
    a, b = radio.popular(limit=20), radio.popular(limit=20)
    assert a == b and a[10] is not b[10]


def test_dead_network_falls_back_fast_and_then_instantly(monkeypatch):
    """A dead network must still answer inside the dashboard proxy's 10 s limit, then skip Radio Browser."""
    import time as _time
    from revamp_core import radio
    calls = []

    def dead(req, timeout=None):
        calls.append(timeout)
        raise OSError("unreachable")

    monkeypatch.setattr(radio.urllib.request, "urlopen", dead)
    monkeypatch.setattr(radio, "RADIO_BROWSER_SERVERS", ["http://a", "http://b", "http://c"])
    out = radio.popular()
    assert out and all(t <= radio.RB_TOTAL_BUDGET for t in calls)
    n = len(calls)
    start = _time.monotonic()
    assert radio.popular()
    assert _time.monotonic() - start < 0.5 and len(calls) == n  # cooldown: no new network calls
