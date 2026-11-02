"""Browser e2e (11-01): Radio keeps working when Radio Browser drops a request.

User report: on Windows and Linux the radio's Browse list stopped scrolling, genre
search didn't work, and "it's not contacting the servers". Radio Browser is down to
one server that drops a share of connections; one failed page used to end the
endless list for good. Here Radio Browser is faked in-process: the second page fails
once, and the list must offer a retry and then carry on. A plain search must offer
its genre.
"""
import json
import threading
import urllib.parse

import pytest

pytest.importorskip("websocket")
from test_nav_rail_e2e import Page, free_port  # noqa: E402  (same headless, muted Chrome)

from revamp_core import radio  # noqa: E402


class _Resp:
    def __init__(self, body, total):
        self._body = json.dumps(body).encode()
        self.headers = {"x-rbtotal": str(total)}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _station(i, tag):
    return {"stationuuid": f"s{i}", "name": f"Station {i} {tag}", "url": f"https://stream.example.com/{i}",
            "url_resolved": f"https://stream.example.com/{i}", "tags": tag, "codec": "MP3",
            "bitrate": 128, "countrycode": "US", "lastcheckok": 1}


@pytest.fixture()
def fake_rb(monkeypatch):
    lock = threading.Lock()
    state = {"failed_second_page": False, "requests": []}

    def fake(req, timeout=None):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        with lock:
            state["requests"].append(q)
            offset = int(q.get("offset", ["0"])[0])
            if offset > 0 and not state["failed_second_page"]:
                state["failed_second_page"] = True
                raise ConnectionResetError(104, "Connection reset by peer")
        tag = q.get("tag", q.get("name", ["x"]))[0]
        limit = int(q.get("limit", ["100"])[0])
        rows = [_station(offset + i, tag) for i in range(limit)] if offset < 300 else []
        return _Resp(rows, 300)

    monkeypatch.setattr(radio, "_rb_open", fake)
    monkeypatch.setattr(radio, "RB_RETRY_PAUSE", 0)
    # One name, one round: the server's own retry would hide the dropped page, and the
    # page-level retry is what is under test here.
    monkeypatch.setattr(radio, "RADIO_BROWSER_SERVERS", ["https://de1.api.radio-browser.info"])
    monkeypatch.setattr(radio, "RB_ROUNDS", 1)
    monkeypatch.setattr(radio, "_rb_down_until", 0)
    monkeypatch.setattr(radio, "RB_DOWN_COOLDOWN", 0)
    radio._reset_caches()
    yield state
    radio._reset_caches()


def _open_search(page, query):
    # The boot opens its first view a moment after the nav appears: let it, then switch.
    page.wait("window.revampPlayerApi && document.getElementById('panel-title').textContent === 'Music'", 40)
    page.wait("""(() => { document.querySelector('#nav button[data-view="radio"]').click();
        return document.getElementById('panel-title').textContent === 'Radio' && document.querySelector('button[data-t="search"]'); })()""")
    page.eval("document.querySelector('button[data-t=\"search\"]').click()")
    page.wait("document.querySelector('input[type=search]')")
    page.eval(f"""(() => {{ const i = document.querySelector('input[type=search]');
        i.value = {json.dumps(query)}; i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()""")


def test_a_dropped_page_retries_and_the_list_keeps_scrolling(server, fake_rb):
    page = Page(free_port(), 1100, 800, f"{server.client.base}/", __import__("pathlib").Path("/tmp"))
    try:
        _open_search(page, "#jazz")
        page.wait("document.body.innerText.includes('Station 49 jazz')", 20)
        # Scroll to the end: the second page fails once.
        page.eval("document.querySelector('.panel-body, #panel-body').scrollTop = 1e9")
        page.wait("document.querySelector('.radio-retry') || document.body.innerText.includes('Station 60 jazz')", 20)
        # Either the automatic retry already ran or the retry row is showing; both must
        # end with more stations than the first page.
        page.wait("document.body.innerText.includes('Station 60 jazz')", 30)
        assert fake_rb["failed_second_page"]
        assert not page.eval("Boolean(document.querySelector('.radio-retry'))")
        # And scrolling still goes on after the retry.
        page.eval("document.querySelector('.panel-body, #panel-body').scrollTop = 1e9")
        page.wait("document.body.innerText.includes('Station 110 jazz')", 20)
    finally:
        page.close()


def test_a_plain_search_offers_its_genre(server, fake_rb):
    page = Page(free_port(), 1100, 800, f"{server.client.base}/", __import__("pathlib").Path("/tmp"))
    try:
        _open_search(page, "Jazz")
        page.wait("[...document.querySelectorAll('button')].some(b => b.textContent.startsWith('Genre: jazz'))", 20)
        page.eval("[...document.querySelectorAll('button')].find(b => b.textContent.startsWith('Genre: jazz')).click()")
        page.wait("document.querySelector('input[type=search]').value === '#jazz'")
        page.wait("document.body.innerText.includes('Station 0 jazz')", 20)
        assert any(q.get("tag") == ["jazz"] for q in fake_rb["requests"])
    finally:
        page.close()


def test_radio_browser_down_does_not_retry_forever(server, monkeypatch):
    # A first-page failure offers "Try again". Retrying on its own there rebuilt the list,
    # whose new first page retried again: a request every few seconds while Radio Browser
    # was down, in every open window.
    calls = []

    def down(req, timeout=None):
        calls.append(req.full_url)
        raise ConnectionResetError(104, "Connection reset by peer")

    monkeypatch.setattr(radio, "_rb_open", down)
    monkeypatch.setattr(radio, "RADIO_BROWSER_SERVERS", ["https://de1.api.radio-browser.info"])
    monkeypatch.setattr(radio, "RB_ROUNDS", 1)
    monkeypatch.setattr(radio, "RB_DOWN_COOLDOWN", 0)
    monkeypatch.setattr(radio, "_rb_down_until", 0)
    radio._reset_caches()
    page = Page(free_port(), 1100, 800, f"{server.client.base}/", __import__("pathlib").Path("/tmp"))
    try:
        _open_search(page, "#jazz")
        page.wait("document.querySelector('.radio-retry')", 20)
        first = len(calls)
        import time as _t
        _t.sleep(10)
        assert len(calls) == first, f"kept retrying on its own: {first} -> {len(calls)}"
        page.eval("document.querySelector('.radio-retry button').click()")
        page.wait(f"true", 2)
        _t.sleep(1.5)
        assert len(calls) > first  # the button still works
    finally:
        page.close()
        radio._reset_caches()
