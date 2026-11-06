"""Books in several files, end to end in a real (muted, headless) Chrome: start the book from
the Books list and it goes on in the part you were in, at your place; Previous goes back
without bouncing; music plays as before (user report 11-03, rounds 11-14)."""
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
CHROME = next((p for p in ("/opt/google/chrome/chrome", shutil.which("google-chrome") or "",
                           shutil.which("chromium") or "") if p and os.path.exists(p)), None)
ROOT = Path(__file__).resolve().parents[1]


def _wav(path: Path, seconds: int) -> None:
    with wave.open(str(path), "wb") as w:  # 8 kHz, 8-bit mono silence: small and always playable
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(b"\x80" * 8000 * seconds)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.skipif(not CHROME, reason="needs Chrome")
def test_a_book_in_several_files_continues_and_previous_does_not_bounce(tmp_path):
    books = tmp_path / "books" / "Saga"
    books.mkdir(parents=True)
    for n in (1, 2, 3):
        _wav(books / f"Part {n}.wav", 90)
    music = tmp_path / "music"
    music.mkdir()
    _wav(music / "Song.wav", 20)
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"library_folders": [
        {"path": str(tmp_path / "books"), "kind": "books"}, {"path": str(music), "kind": "music"}]}))
    port = _free_port()
    env = {**os.environ, "REVAMP_HOME": str(data), "REVAMP_PORT": str(port)}
    env.pop("REVAMP_LIBRARY_DIRS", None)
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
        parts = []
        for _ in range(120):
            found = call("/api/library/tracks?kind=audiobook&limit=50", tok=tok)
            rows = found.get("tracks", found) if isinstance(found, dict) else found
            parts = sorted((r for r in rows if "Saga" in r.get("path", "")), key=lambda r: r["path"])
            if len(parts) == 3 and not call("/api/library/status", tok=tok).get("scanning"):
                break
            time.sleep(0.5)
        assert len(parts) == 3, parts
        ids = [p["id"] for p in parts]
        # Yesterday: part 3, 40 s in.
        call("/api/resume", {"key": f"local:track:{ids[2]}", "position": 40}, tok=tok)

        ch = subprocess.Popen([CHROME, "--headless=new", "--mute-audio", "--autoplay-policy=no-user-gesture-required",
                               f"--user-data-dir={prof}", "--remote-debugging-port=0", "--remote-allow-origins=*",
                               "--window-size=1200,900", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(150):  # a busy CI machine can take well over 10 s to start Chrome
            if os.path.exists(prof + "/DevToolsActivePort"):
                break
            time.sleep(0.2)
        dport = open(prof + "/DevToolsActivePort").read().split()[0]
        seq = [0]

        def tab(url):
            t = json.load(urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{dport}/json/new?{url}", method="PUT")))
            return websocket.create_connection(t["webSocketDebuggerUrl"])

        def ev(ws, expr):
            seq[0] += 1
            ws.send(json.dumps({"id": seq[0], "method": "Runtime.evaluate",
                                "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == seq[0]:
                    return r.get("result", {}).get("result", {}).get("value")

        def now():
            np = (call("/api/state", tok=tok).get("live") or {}).get("now_playing") or {}
            return np.get("track_id"), np.get("position") or 0, np.get("status")

        def wait_for(pred, seconds=30):
            last = None
            for _ in range(int(seconds / 0.25)):
                last = now()
                if pred(*last):
                    return last
                time.sleep(0.25)
            raise AssertionError(f"timed out; last now_playing = {last}")

        player = tab(f"{base}/?mode=full")
        for _ in range(120):
            if (call("/api/state", tok=tok).get("live") or {}).get("player_connected"):
                break
            time.sleep(0.25)
        # Start the book from the first part exactly as the Books list does (views/library.js
        # trackToItem + loadTracks replace).
        def as_items(rows):
            return json.dumps([{"kind": "track", "title": r.get("title"), "artist": r.get("artist") or "",
                                "album": r.get("album") or "", "url": r.get("url"), "duration": r.get("duration"),
                                "track_id": r["id"], **({"book": r["kind"] == "audiobook"} if r.get("kind") else {})}
                               for r in rows])
        items = as_items(parts)
        assert ev(player, f"window.revampPlayerApi.loadTracks({items}, {{mode: 'replace', play: true}}).then(() => 'ok')") == "ok"

        # 1. It goes on in part 3 at about 0:40, not part 1 from the start.
        tid, pos, _ = wait_for(lambda t, p, s: t == ids[2] and p >= 38 and s == "PLAYING")
        assert pos < 60

        # 2. Previous goes to part 2 and stays there (no bounce back to part 3).
        call("/api/command", {"action": "prev"}, tok=tok)
        wait_for(lambda t, p, s: t == ids[1] and s == "PLAYING")
        time.sleep(4)
        assert now()[0] == ids[1], "Previous bounced back"

        # 3. Previous again: part 1 plays as asked, from its start, no continue.
        call("/api/command", {"action": "prev"}, tok=tok)
        wait_for(lambda t, p, s: t == ids[0] and s == "PLAYING")
        time.sleep(4)
        tid, pos, _ = now()
        assert tid == ids[0] and pos < 15, "part 1 reached inside the book must play as asked"

        # 4. Music is untouched: a song plays from its start.
        song = call("/api/library/tracks?kind=music&limit=10", tok=tok)
        srows = song.get("tracks", song) if isinstance(song, dict) else song
        sid = srows[0]["id"]
        sitem = as_items(srows[:1])
        assert ev(player, f"window.revampPlayerApi.loadTracks({sitem}, {{mode: 'replace', play: true}}).then(() => 'ok')") == "ok"
        tid, pos, _ = wait_for(lambda t, p, s: t == sid and s == "PLAYING")
        assert pos < 10

        # 5. The way a person starts a book in Hermes: double-click part 1 in the Books list of
        # the library page (two server commands once; Grok a10r14). It goes on in part 3.
        call("/api/resume", {"key": f"local:track:{ids[2]}", "position": 50}, tok=tok)
        control = tab(f"{base}/?control=1")
        for _ in range(80):
            if ev(control, "!!window.revampSetView"):
                break
            time.sleep(0.25)
        ev(control, "window.revampSetView('books'), true")
        for _ in range(80):
            if ev(control, "[...document.querySelectorAll('tbody tr')].some(r => r.textContent.includes('Part 1'))"):
                break
            time.sleep(0.25)
        assert ev(control, """(() => { const r = [...document.querySelectorAll('tbody tr')].find(r => r.textContent.includes('Part 1'));
          r.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); return 'ok' })()""") == "ok"
        tid, pos, _ = wait_for(lambda t, p, s: t == ids[2] and p >= 48 and s == "PLAYING")
        assert pos < 70

        # 6. A slow continue lookup must never undo the person's own seek (codex a10r16/a10r17:
        # Webamp's own seek bar does not bump actSeq, so the resume pulled playback back).
        song_items = as_items(srows[:1])
        book_items = as_items(parts)

        def start_part_one_after_a_song():
            # A song just before makes "start at part 1" a real continue (from= a song), not
            # "part 1 reached from inside the book", which plays as asked.
            assert ev(player, f"window.revampPlayerApi.loadTracks({song_items}, {{mode: 'replace', play: true}}).then(() => 'ok')") == "ok"
            wait_for(lambda t, p, s: t == sid and s == "PLAYING")
            # Set the places now, while only the song plays: a book part still playing would save
            # over part 1's older place and turn the continue into "part 1 holds the place".
            call("/api/resume", {"key": f"local:track:{ids[0]}", "position": 40}, tok=tok)
            call("/api/resume", {"key": f"local:track:{ids[2]}", "position": 45}, tok=tok)
            want = call(f"/api/resume/book?id={ids[0]}&from={sid}", tok=tok)
            assert want.get("id") == ids[2], f"the setup must be a real continue: {want}"
            assert ev(player, f"window.revampPlayerApi.loadTracks({book_items}, {{mode: 'replace', play: true}}).then(() => 'ok')") == "ok"
            wait_for(lambda t, p, s: t == ids[0] and s == "PLAYING" and p < 20)

        assert ev(player, """(() => { const f = window.fetch; window.__bookDelay = 3000;
            window.fetch = (u, o) => String(u).includes('/api/resume/book')
              ? new Promise((r) => setTimeout(() => r(f(u, o)), window.__bookDelay)) : f(u, o); return 'ok' })()""") == "ok"
        start_part_one_after_a_song()
        # The person drags Webamp's own seek bar while the continue lookup is still in flight.
        assert ev(player, "window.__webamp.seekToTime(60), 'ok'") == "ok"
        time.sleep(6)
        tid, pos, _ = now()
        assert tid == ids[0], "the pending continue jumped to another part after the person seeked"
        assert pos >= 55, f"the pending resume pulled back to the saved place: {pos}"

        # 6b. The same with a native seek that stays inside the opening 15 s (codex a10r17: the
        # jump guard only looked at "under 15 s", so a nudge from 0:01 to 0:10 still jumped).
        start_part_one_after_a_song()
        assert ev(player, "window.__webamp.seekToTime(10), 'ok'") == "ok"
        time.sleep(6)
        tid, pos, _ = now()
        assert tid == ids[0] and pos < 35, f"a small native seek was overridden: {tid} {pos}"

        # 7. A slow lookup must not look like the person seeking (claude a10r19): playing part 2
        # (never a continue) with a 6 s delay still seeks to its own saved place, and the place
        # stays 40 instead of being overwritten by the opening seconds.
        ev(player, "window.__bookDelay = 6000, 'ok'")
        call("/api/resume", {"key": f"local:track:{ids[1]}", "position": 40}, tok=tok)
        assert ev(player, f"window.revampPlayerApi.loadTracks({book_items}, {{mode: 'replace', play: true}}).then(() => 'ok')") == "ok"
        wait_for(lambda t, p, s: t == ids[0] and s == "PLAYING" and p < 20)
        call("/api/command", {"action": "next"}, tok=tok)   # land on part 2 through the queue
        wait_for(lambda t, p, s: t == ids[1] and s == "PLAYING")
        time.sleep(9)
        tid, pos, _ = now()
        assert tid == ids[1], f"the slow lookup moved the person off part 2: {tid}"
        assert pos >= 38, f"a slow lookup was mistaken for a seek; playback stayed at {pos}"
        assert call("/api/resume?key=" + f"local:track:{ids[1]}", tok=tok).get("position", 0) >= 38, \
            "the opening seconds overwrote the saved place"

        # 8. A pause while the lookup is still out keeps the saved place, and Play then goes to it
        # (codex a10r21: the opening second replaced a 40 s place, and Play never looked again).
        def clock():
            return ev(player, "window.__webamp.store.getState().media.timeElapsed") or 0

        def wait_clock(expr, seconds=15):
            for _ in range(int(seconds / 0.02)):
                if ev(player, expr):
                    return
                time.sleep(0.02)
            raise AssertionError(f"timed out on {expr}; clock {clock()}")

        start_part_one_after_a_song()   # part 1 at 0:40, part 3 at 0:45 newest: a real continue
        assert ev(player, "window.__webamp.pause(), 'ok'") == "ok"
        time.sleep(0.2)
        time.sleep(9)   # the lookup comes back and two save rounds go by, still paused
        saved = call("/api/resume?key=" + f"local:track:{ids[0]}", tok=tok).get("position", 0)
        assert saved >= 38, f"pausing during the lookup erased the saved place: {saved}"
        assert ev(player, "window.__webamp.play(), 'ok'") == "ok"
        wait_for(lambda t, p, s: t == ids[2] and s == "PLAYING" and p >= 43)   # goes on in part 3

        # 9. A retry of a dropped seek must not undo the person's own seek (grok a10r21): the page's
        # first seek is lost, the person scrubs to 0:20, and they stay there.
        ev(player, "window.__bookDelay = 0, 'ok'")
        call("/api/resume", {"key": f"local:track:{ids[2]}", "position": 50}, tok=tok)
        assert ev(player, """(() => { const w = window.__webamp, real = w.seekToTime.bind(w);
            window.__realSeek = real; let left = 1;
            w.seekToTime = (t) => { if (left > 0) { left -= 1; return; } real(t); }; return 'ok' })()""") == "ok"
        last = as_items(parts[2:])
        assert ev(player, f"window.revampPlayerApi.loadTracks({last}, {{mode: 'replace', play: true}}).then(() => 'ok')") == "ok"
        wait_clock("window.__webamp.seekToTime !== window.__realSeek && window.__webamp.store.getState().media.timeElapsed > 0")
        time.sleep(0.6)   # the first (dropped) seek has been tried by now
        assert ev(player, "window.__realSeek(20), 'ok'") == "ok"
        time.sleep(6)     # long enough for every retry
        tid, pos, _ = now()
        assert tid == ids[2] and pos < 45, f"a retry pulled the person back to the saved place: {tid} {pos}"

        # 10. Real pointer interaction at the start of Webamp's own seek bar, after a
        # dropped resume seek. A clock-only jump heuristic cannot distinguish this from
        # buffering. Count attempts so a coincidentally low playhead cannot mask a retry.
        assert ev(player, "window.__webamp.seekToTime = window.__realSeek, 'ok'") == "ok"
        assert ev(player, f"window.revampPlayerApi.loadTracks({song_items}, {{mode: 'replace', play: true}}).then(() => 'ok')") == "ok"
        wait_for(lambda t, p, s: t == sid and s == "PLAYING")
        call("/api/resume", {"key": f"local:track:{ids[2]}", "position": 50}, tok=tok)
        assert ev(player, """(() => { const w = window.__webamp, real = w.seekToTime.bind(w);
            window.__attempts = 0; w.seekToTime = (t) => { window.__attempts++;
              if (window.__attempts > 1) real(t); }; return 'ok' })()""") == "ok"
        assert ev(player, f"window.revampPlayerApi.loadTracks({last}, {{mode: 'replace', play: true}}).then(() => 'ok')") == "ok"
        wait_clock("window.__attempts === 1 && window.__webamp.store.getState().media.timeElapsed > 0")
        rect = ev(player, """(() => { const el = document.querySelector('#position');
            el.addEventListener('pointerup', e => { window.__nativeSeekPointer = e.isTrusted; }, {once: true});
            const r = el.getBoundingClientRect();
            return {x: r.x + 2, y: r.y + r.height / 2, width: r.width}; })()""")
        assert rect and rect['width'] > 10, f"seek bar not visible: {rect}"
        for kind in ('mousePressed', 'mouseReleased'):
            seq[0] += 1
            player.send(json.dumps({'id': seq[0], 'method': 'Input.dispatchMouseEvent', 'params': {
                'type': kind, 'x': rect['x'], 'y': rect['y'], 'button': 'left', 'clickCount': 1}}))
            while json.loads(player.recv()).get('id') != seq[0]:
                pass
        assert ev(player, 'window.__nativeSeekPointer') is True, 'trusted pointer did not reach seek bar'
        time.sleep(6)
        assert ev(player, 'window.__attempts') == 1, 'resume retried after the native pointer seek'
        assert clock() < 15, f"resume overrode native pointer seek: {clock()}"

        # 11. Paused while a resume is held, the person drags the seek bar: that place is saved
        # right away (they may close the player now), and Play leaves them there (grok a10r21).
        assert ev(player, "window.__webamp.seekToTime = window.__realSeek, window.__bookDelay = 3000, 'ok'") == "ok"
        start_part_one_after_a_song()
        assert ev(player, "window.__webamp.pause(), 'ok'") == "ok"
        time.sleep(4.5)   # the lookup has come back and the resume is held for Play
        assert ev(player, "window.__webamp.store.dispatch({type: 'SEEK_TO_PERCENT_COMPLETE', percent: 20}), 'ok'") == "ok"
        time.sleep(1.5)
        saved = call("/api/resume?key=" + f"local:track:{ids[0]}", tok=tok).get("position", 0)
        assert 15 <= saved <= 21, f"the seek made during the hold was not saved: {saved}"
        assert ev(player, "window.__webamp.play(), 'ok'") == "ok"
        time.sleep(4)
        tid, pos, status = now()
        assert tid == ids[0] and status == "PLAYING" and pos < 35, f"Play did not stay at the seek: {tid} {pos} {status}"

    finally:
        if ch:
            ch.terminate()
            ch.wait()
        srv.terminate()
        srv.wait()
        shutil.rmtree(prof, True)
