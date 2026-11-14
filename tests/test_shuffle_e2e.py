"""Shuffle, end to end in a real (muted, headless) Chrome: a dealt deck, not Webamp's random pick.

Measured before (2026-11-14): 12 Nexts on a 12-song queue heard 6 distinct songs, Previous jumped
somewhere random, and REVamp's shuffle button seemed to do nothing. Now every song plays once per
pass, Previous retraces what was heard, the audio the player loads is the song it reports, a pass
that plays out by itself stops at its end (Repeat off), and Webamp's own shuffle button is lit and
toggles REVamp's shuffle."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

import pytest

websocket = pytest.importorskip("websocket")
CHROME = next((p for p in (os.environ.get("CHROME_BIN") and shutil.which(os.environ["CHROME_BIN"]) or "",
                           "/opt/google/chrome/chrome", shutil.which("google-chrome") or "",
                           shutil.which("chromium") or "",
                           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                           r"C:\Program Files\Google\Chrome\Application\chrome.exe")
               if p and os.path.exists(p)), None)
ROOT = Path(__file__).resolve().parents[1]
N = 12
# The browser runs with the machine's own environment, read at import (before conftest's
# throwaway HOME/USERPROFILE): Edge on Windows does not start under a fake USERPROFILE.
BROWSER_ENV = dict(os.environ)

AUDIO_SRC = """(() => { const m = window.__webamp.media || {};
  const el = m._source?._audio || m._audio || m._source?._element || null;
  return el ? String(el.currentSrc || el.src || '') : ''; })()"""


def _wav(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as w:  # 8 kHz, 8-bit mono silence: small and always playable
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(b"\x80" * int(8000 * seconds))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.skipif(not CHROME, reason="needs Chrome")
def test_shuffle_deals_every_song_once_and_previous_retraces(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    for i in range(N):
        _wav(music / f"Song {i:02d}.wav", 2)
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"library_folders": [{"path": str(music), "kind": "music"}]}))
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("REVAMP_LIBRARY_DIRS", "HERMES_HOME")}
    env.update(REVAMP_HOME=str(data), REVAMP_PORT=str(port))
    srv = subprocess.Popen([sys.executable, "-m", "revamp_core.server", "--port", str(port), "--data", str(data)],
                           cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"

    def call(path, body=None, tok=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={**({"X-Revamp-Token": tok} if tok else {}),
                                              **({"Content-Type": "application/json"} if body is not None else {})})
        return json.load(urllib.request.urlopen(req, timeout=5))

    prof = tempfile.mkdtemp(dir=str(tmp_path))
    ch = None
    try:
        for _ in range(60):
            try:
                call("/api/health")
                break
            except Exception:
                time.sleep(0.3)
        tok = (data / "token").read_text().strip()
        rows = []
        for _ in range(120):
            found = call("/api/library/tracks?kind=music&limit=100", tok=tok)
            rows = found.get("tracks", found) if isinstance(found, dict) else found
            if len(rows) == N and not call("/api/library/status", tok=tok).get("scanning"):
                break
            time.sleep(0.5)
        assert len(rows) == N, rows
        rows = sorted(rows, key=lambda r: r["path"])

        ch = subprocess.Popen([CHROME, "--headless=new", "--mute-audio", "--autoplay-policy=no-user-gesture-required",
                               "--no-first-run", "--no-default-browser-check",
                               f"--user-data-dir={prof}", "--remote-debugging-port=0", "--remote-allow-origins=*",
                               "--window-size=1200,900", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=BROWSER_ENV)
        for _ in range(300):  # a busy machine, or a first run of a new profile, can take a while
            if os.path.exists(os.path.join(prof, "DevToolsActivePort")):
                break
            time.sleep(0.2)
        dport = open(os.path.join(prof, "DevToolsActivePort")).read().split()[0]
        t = json.load(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{dport}/json/new?{base}/?mode=full", method="PUT")))
        ws = websocket.create_connection(t["webSocketDebuggerUrl"])
        seq = [0]

        def ev(expr):
            seq[0] += 1
            ws.send(json.dumps({"id": seq[0], "method": "Runtime.evaluate",
                                "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == seq[0]:
                    return r.get("result", {}).get("result", {}).get("value")

        for _ in range(120):
            if (call("/api/state", tok=tok).get("live") or {}).get("player_connected"):
                break
            time.sleep(0.25)
        items = json.dumps([{"kind": "track", "title": r.get("title"), "artist": "", "url": r.get("url"),
                             "duration": r.get("duration"), "track_id": r["id"]} for r in rows])
        assert ev(f"window.revampPlayerApi.loadTracks({items}, {{mode: 'replace', play: true}}).then(() => 'ok')") == "ok"
        time.sleep(1.5)

        def playing():
            """(the song the player reports, the song its audio element loaded)"""
            cur = ev("window.__webamp.store.getState().playlist.currentTrack")
            order = ev("window.__webamp.store.getState().playlist.trackOrder")
            src = ev(AUDIO_SRC) or ""
            shown = rows[order.index(cur)]["id"] if cur in (order or []) else None
            heard = next((r["id"] for r in rows if f"/{r['id']}" in src), None)
            return shown, heard

        # Shuffle on, the way REVamp's buttons send it.
        call("/api/command", {"action": "shuffle", "on": True}, tok=tok)
        for _ in range(20):
            if ((call("/api/state", tok=tok).get("live") or {}).get("now_playing") or {}).get("shuffle"):
                break
            time.sleep(0.25)
        assert ((call("/api/state", tok=tok).get("live") or {}).get("now_playing") or {}).get("shuffle") is True
        assert ev("window.__webamp.isShuffleEnabled()") is False, "Webamp's random pick must stay off"
        assert ev("document.querySelector('#webamp #shuffle').classList.contains('selected')") is True

        # 1. A pass: the current song plus 11 Nexts plays all 12, and the audio is the song shown.
        heard = [playing()]
        for _ in range(N - 1):
            ev("window.revampPlayerApi.next(), true")
            time.sleep(0.3)
            heard.append(playing())
        assert all(s == h for s, h in heard), f"the audio is not the song shown: {heard}"
        ids = [s for s, _ in heard]
        assert len(set(ids)) == N, f"a pass repeated songs: {ids}"
        assert ids != [r["id"] for r in rows], "shuffle played the queue in order"

        # 2. Previous retraces what was heard.
        for back in (ids[-2], ids[-3], ids[-4]):
            ev("window.revampPlayerApi.prev(), true")
            time.sleep(0.3)
            shown, h = playing()
            assert shown == back == h, f"Previous went to {shown} (heard {h}), expected {back}"

        # 3. Next after the last card deals a fresh pass (Next is never a dead button).
        for _ in range(3):
            ev("window.revampPlayerApi.next(), true")
            time.sleep(0.3)
        assert playing()[0] == ids[-1]
        ev("window.revampPlayerApi.next(), true")
        time.sleep(0.3)
        shown, h = playing()
        assert shown == h and shown is not None and shown != ids[-1], "Next at the end of a pass did nothing"

        # 4. A pass that plays out by itself deals every song, then stops (Repeat off).
        ev("window.revampPlayerApi.setShuffle(false), window.revampPlayerApi.setShuffle(true), true")
        for _ in range(int(N * 2 * 2.5) + 20):
            if ev("window.__webamp.store.getState().media.status") == "ENDED":
                break
            time.sleep(0.5)
        state = ev("window.revampPlayerApi.shuffleState()")
        assert ev("window.__webamp.store.getState().media.status") == "ENDED", "the pass never ended"
        dealt = state["order"][: state["pos"] + 1]
        assert len(dealt) == N and len(set(dealt)) == N, f"songs ending by themselves skipped or repeated: {state}"

        # 5. Webamp's own shuffle button toggles REVamp's shuffle.
        ev("document.querySelector('#webamp #shuffle').click(), true")
        time.sleep(1.0)
        assert ev("window.revampPlayerApi.shuffleState().on") is False
        assert ev("document.querySelector('#webamp #shuffle').classList.contains('selected')") is False
        ev("document.querySelector('#webamp #shuffle').click(), true")
        time.sleep(1.0)
        assert ev("window.revampPlayerApi.shuffleState().on") is True
    finally:
        if ch:
            ch.terminate()
            ch.wait()
        srv.terminate()
        srv.wait()
        shutil.rmtree(prof, True)
