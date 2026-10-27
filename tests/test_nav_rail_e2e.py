"""Browser e2e (2026-10-26 compactnav lane): the nav sidebar must always be visible.

Below 900px and in Hermes embed mode the sidebar used to be `display:none` with an
empty `#tabbar` fallback, so every view but the default was unreachable. Now the
sidebar collapses to the same 56px icon rail the 900-1099px band uses. This drives a
real server plus headless Chrome over CDP and asserts the rail is visible, labelled
and clickable at narrow and wide widths, and in forced embed mode.
"""
import base64
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
websocket = pytest.importorskip("websocket")  # websocket-client: optional, skip where absent (CI)

ROOT = Path(__file__).resolve().parents[1]
CHROME = os.environ.get("CHROME_BIN", "google-chrome")
TOKEN = None  # set by the fixture; the server injects it into the page on GET /


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Page:
    """One headless Chrome tab driven over the CDP websocket."""

    def __init__(self, chrome_port: int, width: int, height: int, url: str, shot: Path):
        self.proc = subprocess.Popen(
            [
                CHROME, "--headless=new", "--no-sandbox", "--mute-audio",
                "--autoplay-policy=no-user-gesture-required", "--use-gl=angle",
                "--use-angle=swiftshader-webgl", "--enable-unsafe-swiftshader",
                f"--remote-debugging-port={chrome_port}", "--remote-allow-origins=*",
                f"--user-data-dir={self._profile()}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.ws = self._connect(chrome_port, timeout=20)
        self.seq = 0
        self.shot = shot
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )
        self.call("Page.navigate", {"url": url})
        self.wait("document.querySelector('#webamp-host')?.offsetWidth")
        self.wait("document.querySelectorAll('#nav button').length > 0")

    @staticmethod
    def _profile() -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="revamp-e2e-chrome-")

    @staticmethod
    def _connect(chrome_port: int, timeout: float):
        deadline = time.time() + timeout
        last: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{chrome_port}/json/list", timeout=2) as r:
                    targets = json.loads(r.read())
                target = next(t for t in targets if t.get("type") == "page")
                return websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10)
            except Exception as e:  # chrome still starting
                last = e
                time.sleep(0.2)
        raise AssertionError(f"chrome CDP never came up: {last}")

    def call(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        mid = {"id": self.seq, "method": method, "params": params or {}}
        self.ws.send(json.dumps(mid))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid["id"]:
                if "error" in msg:
                    raise AssertionError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expression: str):
        result = self.call(
            "Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True}
        )
        if "exceptionDetails" in result:
            raise AssertionError(f"page JS failed: {result['exceptionDetails']['exception']['description']}")
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.eval(f"Boolean({expression})"):
                return
            time.sleep(0.1)
        raise AssertionError(f"timed out waiting for: {expression}")

    def screenshot(self, name: str):
        result = self.call("Page.captureScreenshot", {"format": "png"})
        self.shot.mkdir(parents=True, exist_ok=True)
        (self.shot / name).write_bytes(base64.b64decode(result["data"]))

    def close(self):
        try:
            self.ws.close()
        except OSError:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("revamp-e2e-data")
    port = free_port()
    proc = subprocess.Popen(
        [
            "python3", "-m", "revamp_core.server", "--port", str(port),
            "--data", str(data), "--library", str(ROOT / "lanes" / "_e2e" / "lib"),
        ],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "REVAMP_HOME": str(data), "REVAMP_PORT": str(port)},
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError("server exited during startup")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("server never became healthy")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def nav_state(page: Page) -> dict:
    return page.eval(
        """(() => {
  const sb = document.getElementById('sidebar');
  const r = sb.getBoundingClientRect();
  const btn = [...document.querySelectorAll('#nav button')].map(b => ({
    view: b.dataset.view,
    label: b.querySelector('.label')?.textContent ?? null,
    visible: b.offsetWidth > 0 && b.offsetHeight > 0,
  }));
  const tabbar = document.getElementById('tabbar');
  return {
    sidebarHidden: getComputedStyle(sb).display === 'none',
    sidebarWidth: Math.round(r.width),
    navButtons: btn,
    tabbarHidden: getComputedStyle(tabbar).display === 'none',
  };
})()"""
    )


def layout_rects(page: Page) -> dict:
    # DOMRect serializes as {} over CDP returnByValue; pick the numeric fields.
    return page.eval(
        """(() => {
  const box = el => { if (!el) return null; const r = el.getBoundingClientRect();
    return {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height}; };
  return {
    sidebar: box(document.getElementById('sidebar')),
    panel: box(document.getElementById('panel')),
    stage: box(document.getElementById('stage')),
    card: box(document.getElementById('np-card')),
    host: box(document.getElementById('webamp-host')),
    mainWin: box(document.getElementById('main-window')),
  };
})()"""
    )


def set_light_theme(page: Page):
    """Force prefers-color-scheme: light via CDP (the page reads it in CSS only)."""
    page.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": "light"}]})


SHOT_DIR = Path(__file__).resolve().parents[1] / "lanes" / "compactnav"


def assert_round2_geometry(page: Page):
    """Round-2 layout: panel right of/below the rail, card inside it, no clipped player top."""
    r = layout_rects(page)
    rail, panel, card = r["sidebar"], r["panel"], r["card"]
    assert rail and panel and card, r
    # 1. the panel never shares the rail column: its left edge is past the rail's right
    #    edge, or it sits on a later grid row entirely (below the stage)
    stage = r["stage"]
    below = panel["top"] >= stage["bottom"] - 1
    beside = panel["left"] >= rail["left"] + rail["width"] - 1
    assert beside or below, {"rail": rail, "panel": panel, "stage": stage}
    if not below:
        assert panel["left"] > rail["left"] + rail["width"] - 1
    # 2. the now-playing card stays inside the rail column
    assert card["right"] <= rail["right"] + 1, {"card": card, "rail": rail}
    # 3. the classic player's top never clips above the stage
    main = r["mainWin"]
    if main and main["height"] > 0:
        assert main["top"] >= stage["top"] - 1, {"main": main, "stage": stage}


@pytest.mark.parametrize("width,expected,embed", [(700, 56, False), (1200, 216, False)])
def test_nav_visible_and_clickable(server, tmp_path, width, expected, embed):
    page = Page(free_port(), width, 800, f"http://127.0.0.1:{server}/", SHOT_DIR if width < 900 else tmp_path)
    try:
        state = nav_state(page)
        assert state["sidebarHidden"] is False
        assert state["sidebarWidth"] == expected, state
        views = [b["view"] for b in state["navButtons"]]
        assert views == ["library", "books", "focus", "radio", "playlists", "skins", "agent"]
        assert all(b["visible"] for b in state["navButtons"]), state
        assert all(b["label"] for b in state["navButtons"])

        # every view reachable through the real click handler
        for view, title in [("books", "Books"), ("radio", "Radio")]:
            page.eval(f"document.querySelector('#nav button[data-view={view}]').click()")
            page.wait(f"document.getElementById('panel-title')?.textContent === {title!r}")
        page.wait("document.getElementById('panel-body')?.innerText.includes('Popular')")

        # keyboard: nav buttons are plain <button>s, focus lands where expected
        page.eval("document.querySelector('#nav button[data-view=books]').focus()")
        assert page.eval("document.activeElement?.dataset.view") == "books"

        page.screenshot(f"nav-{width}.png")
        if width < 900:
            assert_round2_geometry(page)
            page.screenshot(f"v2-{width}.png")
    finally:
        page.close()


@pytest.mark.parametrize("width", [640, 800])
def test_round2_narrow_widths(server, tmp_path, width):
    """Round-2 regression: rail card contained, panel below/beside, player top visible."""
    page = Page(free_port(), width, 800, f"http://127.0.0.1:{server}/", SHOT_DIR)
    try:
        state = nav_state(page)
        assert state["sidebarHidden"] is False
        assert state["sidebarWidth"] == 56, state
        assert all(b["visible"] for b in state["navButtons"]), state
        page.eval("document.querySelector('#nav button[data-view=books]').click()")
        page.wait("document.getElementById('panel-title')?.textContent === 'Books'")
        assert_round2_geometry(page)
        page.screenshot(f"v2-{width}.png")

        # light theme: same geometry under the light token palette
        set_light_theme(page)
        page.wait("matchMedia('(prefers-color-scheme: light)').matches")
        assert_round2_geometry(page)
        page.screenshot(f"v2-{width}-light.png")
    finally:
        page.close()


def test_embed_mode_keeps_nav_rail(server, tmp_path):
    page = Page(free_port(), 640, 620, f"http://127.0.0.1:{server}/?embed=1", SHOT_DIR)
    try:
        state = nav_state(page)
        assert page.eval("document.body.classList.contains('embed')") is True
        assert state["sidebarHidden"] is False
        assert state["sidebarWidth"] == 56, state
        assert all(b["visible"] for b in state["navButtons"]), state
        # the desktop tabbar row is retired; the sidebar rail is the single nav
        assert state["tabbarHidden"] is True

        page.eval("document.querySelector('#nav button[data-view=books]').click()")
        page.wait("document.getElementById('panel-title')?.textContent === 'Books'")
        assert_round2_geometry(page)
        page.screenshot("nav-embed-640.png")
        page.screenshot("v2-embed-640.png")
    finally:
        page.close()


def test_winamp_mode_still_hides_sidebar(server, tmp_path):
    page = Page(free_port(), 900, 620, f"http://127.0.0.1:{server}/?mode=winamp", tmp_path)
    try:
        page.wait("document.body.classList.contains('mode-winamp')")
        state = nav_state(page)
        assert state["sidebarHidden"] is True, state
        page.screenshot("nav-winamp.png")
    finally:
        page.close()
