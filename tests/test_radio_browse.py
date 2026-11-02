"""Endless browse pages, category curation and the new radio routes. All Radio
Browser traffic is faked — no test in here should ever hit the network."""
import socket
import time
import urllib.error

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

    monkeypatch.setattr(radio, "_rb_open", lambda req, timeout=None: Resp())
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
        if "de1.api" in req.full_url:
            raise OSError("wedged node")   # the real-world failure mode
        return Resp()

    monkeypatch.setattr(radio, "_rb_open", fake)
    data, total = radio._rb_json("stations/search", {"limit": 1})
    assert urls[0].startswith("https://de1.api.")
    assert any("de2.api." in u for u in urls)          # fell through to a live mirror
    assert data == [{"url": "https://x/s"}] and total == 42


def test_rb_json_raises_when_every_mirror_is_down(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("dns")

    monkeypatch.setattr(radio, "_rb_open", boom)
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


# ---- 11-01: Radio Browser is down to one server that drops ~1 in 5 connections ----

class _OkResp:
    headers = {"x-rbtotal": "7"}

    def read(self):
        return b'[{"url": "https://x/s"}]'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_rb_json_retries_dropped_connections_instead_of_giving_up(monkeypatch):
    # Every name is the same flaky server now. Trying each name once and then blanking
    # the radio for a minute is what stopped browse, genres and endless scrolling.
    radio._rb_down_until = 0
    monkeypatch.setattr(radio, "RB_RETRY_PAUSE", 0)
    calls = []

    def flaky(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) <= len(radio.RADIO_BROWSER_SERVERS):  # a whole round drops
            raise urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer"))
        return _OkResp()

    monkeypatch.setattr(radio, "_rb_open", flaky)
    data, total = radio._rb_json("stations/search", {"limit": 1})
    assert data == [{"url": "https://x/s"}] and total == 7
    assert radio._rb_down_until == 0


def test_rb_json_offline_machine_still_fails_at_once(monkeypatch):
    # No network: names don't resolve. Retrying those would keep the presets waiting.
    radio._rb_down_until = 0
    calls = []

    def nodns(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))

    monkeypatch.setattr(radio, "_rb_open", nodns)
    t = time.monotonic()
    with pytest.raises(RadioError):
        radio._rb_json("stations/search", {"limit": 1})
    assert time.monotonic() - t < 1.0
    assert len(calls) == len(radio.RADIO_BROWSER_SERVERS)
    radio._rb_down_until = 0


def test_rb_json_bad_request_does_not_blank_the_radio(monkeypatch):
    radio._rb_down_until = 0

    def bad(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(radio, "_rb_open", bad)
    with pytest.raises(RadioError):
        radio._rb_json("stations/search", {"limit": 1})
    assert radio._rb_down_until == 0


def test_cooldown_after_a_real_outage_is_short():
    assert radio.RB_DOWN_COOLDOWN <= 20


def test_default_servers_are_names_that_still_resolve():
    assert not any("nl1." in s or "fi1." in s or "at1." in s for s in radio.RADIO_BROWSER_SERVERS)
    assert radio.RADIO_BROWSER_SERVERS[0].startswith("https://de1.")


def test_connect_tries_ipv4_first_with_a_short_timeout(monkeypatch):
    # Windows with half-working IPv6: Python tried the IPv6 address first and waited the
    # whole budget on it, so Radio Browser never answered there.
    seen = []

    class FakeSock:
        def __init__(self, family, *a):
            self.family = family
            self.timeouts = []

        def settimeout(self, t):
            self.timeouts.append(t)

        def bind(self, a):
            pass

        def connect(self, addr):
            seen.append((self.family, self.timeouts[0]))
            if self.family == socket.AF_INET6:
                raise socket.timeout("v6 route drops packets")

        def close(self):
            pass

    monkeypatch.setattr(radio.socket, "socket", FakeSock)
    answers = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0)),
               (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]
    sock = radio._connect_first(answers, 8.0)
    assert seen[0][0] == socket.AF_INET
    assert sock.family == socket.AF_INET
    assert all(t <= radio.CONNECT_TIMEOUT for _, t in seen)


def test_a_single_address_host_keeps_the_full_connect_timeout(monkeypatch):
    # The short per-address cap is only for moving on to the next address. A host with
    # one address (Radio Browser, many stations) cut off at 2.5 s failed on Windows,
    # which resends a lost SYN after 3 s.
    seen = []

    class FakeSock:
        def __init__(self, family, *a):
            self.t = []

        def settimeout(self, t):
            self.t.append(t)

        def connect(self, addr):
            seen.append(self.t[0])

        def close(self):
            pass

    monkeypatch.setattr(radio.socket, "socket", FakeSock)
    radio._connect_first([(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))], 8.0)
    assert len(seen) == 1 and abs(seen[0] - 8.0) < 0.05


def test_default_timeout_is_kept_not_made_blocking(monkeypatch):
    seen = []

    class FakeSock:
        def __init__(self, family, *a):
            pass

        def settimeout(self, t):
            seen.append(t)

        def connect(self, addr):
            pass

        def close(self):
            pass

    monkeypatch.setattr(radio.socket, "socket", FakeSock)
    monkeypatch.setattr(radio.socket, "getdefaulttimeout", lambda: 12.0)
    radio._connect_first([(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))],
                         socket._GLOBAL_DEFAULT_TIMEOUT)
    assert None not in seen and seen[-1] == 12.0


def test_connecting_stays_inside_the_callers_timeout(monkeypatch):
    # A dead IPv4 (3.5 s) then a slow IPv6 got the full 7 s on top: 10.5 s, past the
    # Hermes proxy's 10 s. The timeout now covers connecting as a whole.
    clock = [100.0]
    monkeypatch.setattr(radio.time, "monotonic", lambda: clock[0])
    seen = []

    class FakeSock:
        def __init__(self, family, *a):
            self.family = family

        def settimeout(self, t):
            self.t = t

        def connect(self, addr):
            seen.append(self.t)
            if self.family == socket.AF_INET:
                clock[0] += self.t  # used its whole slot, then failed
                raise socket.timeout("dead")

        def close(self):
            pass

    monkeypatch.setattr(radio.socket, "socket", FakeSock)
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443)),
               (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))]
    radio._connect_first(answers, 7.0)
    assert seen[0] == radio.CONNECT_TIMEOUT
    assert sum(seen) <= 7.0 + 1e-9


def test_tls_and_reply_share_the_connect_budget(monkeypatch):
    # After a slow connect, TLS and the reply got the full timeout again, so a stall
    # there could run past the Hermes proxy's 10 s.
    clock = [50.0]
    monkeypatch.setattr(radio.time, "monotonic", lambda: clock[0])
    timeouts = []

    class Sock:
        def settimeout(self, t):
            timeouts.append(t)

    def slow_connect(answers, timeout, source=None):
        clock[0] += 5.0
        return Sock()

    class Ctx:
        # Read by http.client on some Python versions (3.11 checks verify_mode).
        verify_mode = __import__("ssl").CERT_REQUIRED
        check_hostname = True
        post_handshake_auth = None

        def wrap_socket(self, sock, server_hostname=None):
            clock[0] += 1.0
            return Sock()

    monkeypatch.setattr(radio, "_connect_first", slow_connect)
    monkeypatch.setattr(radio.socket, "getaddrinfo", lambda *a, **k: [])
    conn = radio._FirstAddressHTTPSConnection("de1.api.radio-browser.info", 443, timeout=7.0, context=Ctx())
    conn.connect()
    assert timeouts == [2.0, 1.0]


def test_a_dropped_ipv4_syn_is_not_reported_as_unreachable(monkeypatch):
    # IPv4 SYN dropped (timeout), then IPv6 "no route": raising only the last error made
    # the server look unreachable, and it was dropped from the retries for the request.
    class FakeSock:
        def __init__(self, family, *a):
            self.family = family

        def settimeout(self, t):
            pass

        def connect(self, addr):
            if self.family == socket.AF_INET:
                raise socket.timeout("SYN dropped")
            raise OSError(radio.errno.ENETUNREACH, "Network is unreachable")

        def close(self):
            pass

    monkeypatch.setattr(radio.socket, "socket", FakeSock)
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443)),
               (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))]
    with pytest.raises(OSError) as info:
        radio._connect_first(answers, 7.0)
    assert not radio._unreachable(info.value)


def test_windows_no_route_codes_count_as_unreachable():
    for name in ("WSAENETUNREACH", "WSAEHOSTUNREACH"):
        code = getattr(radio.errno, name, None) or {"WSAENETUNREACH": 10051, "WSAEHOSTUNREACH": 10065}[name]
        if getattr(radio.errno, name, None) is None:
            continue  # not Windows: the names don't exist here; the source check below covers it
        assert radio._unreachable(OSError(code, "no route"))
    import inspect
    src = inspect.getsource(radio)
    assert '"WSAENETUNREACH"' in src and '"WSAEHOSTUNREACH"' in src


def test_the_pages_automatic_retry_comes_after_the_servers_down_pause():
    # The page retried a failed page after 4 s while the server answered "recently
    # failed" for 15 s, so the one automatic retry never reached Radio Browser.
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "web" / "views" / "radio.js").read_text(encoding="utf-8")
    m = re.search(r"if \(auto && !retried\) \{ retried = true; setTimeout\(go, (\d+)\); \}", js)
    assert m, "auto-retry delay not found in radio.js"
    assert int(m.group(1)) / 1000 > radio.RB_DOWN_COOLDOWN


def test_one_name_answering_4xx_does_not_stop_the_others(monkeypatch):
    # A proxy's 403/404 page on one name aborted the whole request, although the next
    # name would have answered.
    radio._rb_down_until = 0

    def fake(req, timeout=None):
        if "de1." in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return _OkResp()

    monkeypatch.setattr(radio, "_rb_open", fake)
    data, total = radio._rb_json("stations/search", {"limit": 1})
    assert data == [{"url": "https://x/s"}]


def test_each_failed_page_gets_its_own_automatic_retry():
    # The one automatic retry was per list: after page 2 failed and recovered, a failure
    # on page 3 got none. It is re-armed after every page that loads.
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "web" / "views" / "radio.js").read_text(encoding="utf-8")
    load = js[js.index("    async function loadNext() {"):js.index("    loadNext();\n    return getList;")]
    ok = load.index("const found = page.stations || [];")
    assert "retried = false;" in load[load.index("hideSpinner();", load.index("loading = false;\n      if (gen !== listGen) return;\n      hideSpinner();")):ok]


def test_every_live_name_gets_a_turn_after_one_is_dropped(monkeypatch):
    # [de1, de2, all]: de1 resets, de2 is dropped (404). The rotation then tried de1 again
    # and never asked "all", the one name that would have answered.
    radio._rb_down_until = 0
    monkeypatch.setattr(radio, "RB_RETRY_PAUSE", 0)
    urls = []

    def fake(req, timeout=None):
        urls.append(req.full_url)
        if "de1." in req.full_url:
            raise urllib.error.URLError(ConnectionResetError(104, "reset"))
        if "de2." in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return _OkResp()

    monkeypatch.setattr(radio, "_rb_open", fake)
    data, _ = radio._rb_json("stations/search", {"limit": 1})
    assert data == [{"url": "https://x/s"}]
    assert ["de1" in u for u in urls[:3]] == [True, False, False]


def test_a_name_listed_twice_cannot_make_the_retry_loop_spin(monkeypatch):
    # [de1, de1] with de1 unresolvable: the rotation looked for a name not yet dropped,
    # found none, and spun forever at 100% CPU.
    import threading
    radio._rb_down_until = 0
    monkeypatch.setattr(radio, "RADIO_BROWSER_SERVERS", ["https://de1.api.radio-browser.info"] * 2)

    def nodns(req, timeout=None):
        raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))

    monkeypatch.setattr(radio, "_rb_open", nodns)
    out = {}

    def run():
        try:
            radio._rb_json("stations/search", {"limit": 1})
        except RadioError as exc:
            out["err"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(3)
    assert not t.is_alive(), "retry rotation spun forever on a duplicated name"
    assert "err" in out
    radio._rb_down_until = 0


def test_the_server_list_is_deduplicated():
    import inspect
    assert "list(dict.fromkeys(RADIO_BROWSER_SERVERS))" in inspect.getsource(radio)


def test_one_wedged_name_cannot_use_the_whole_budget(monkeypatch):
    # de1 silently drops packets: the first attempt got ~7 s, and de2/all were never asked.
    radio._rb_down_until = 0
    monkeypatch.setattr(radio, "RB_RETRY_PAUSE", 0)
    seen = []

    def fake(req, timeout=None):
        seen.append((req.full_url, timeout))
        if "de1." in req.full_url:
            raise urllib.error.URLError(socket.timeout("timed out"))
        return _OkResp()

    monkeypatch.setattr(radio, "_rb_open", fake)
    radio._rb_json("stations/search", {"limit": 1})
    assert seen[0][1] <= radio.RB_ATTEMPT_CAP <= 4.0
    assert any("de2." in u for u, _ in seen)  # the next name was asked


def test_popular_offers_try_again_when_it_fails():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "web" / "views" / "radio.js").read_text(encoding="utf-8")
    pop = js[js.index("  function loadPopular() {"):js.index("  async function loadFavorites() {")]
    assert "again: loadPopular," in pop
