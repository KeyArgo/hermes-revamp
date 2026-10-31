"""The small spectrum under the time must animate while music plays, including after MilkDrop.

Regression: a bundle patch handed Webamp's style selector the whole store instead of
state.display, so the analyzer got no mode, built its do-nothing painter and showed a
flat line forever. Text checks on the bundle passed; only a real render shows it.
"""
import json, math, os, shutil, socket, struct, subprocess, sys, tempfile, time, urllib.request, wave
from pathlib import Path
import pytest

websocket = pytest.importorskip("websocket")
CHROME = next((p for p in (os.environ.get("CHROME_BIN") and shutil.which(os.environ["CHROME_BIN"]) or "",
                           "/opt/google/chrome/chrome", shutil.which("google-chrome") or "",
                           shutil.which("chromium") or "") if p and os.path.exists(p)), None)
ROOT = Path(__file__).resolve().parents[1]
FRAME = """(()=>{const c=document.querySelector('.revamp-minivis') || document.querySelector('#webamp canvas#visualizer'); if(!c) return null;
  const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let h=0;
  for(let i=0;i<d.length;i+=4) h=(h*31+d[i]+d[i+1]*3+d[i+2]*7)%1000003; return h})()"""

BUDGET = """(()=>{for (const c of document.querySelectorAll('#webamp canvas:not(#visualizer):not(#eqGraph)')) {
  const key=Object.keys(c).find(k=>k.startsWith('__reactFiber$')); let f=c[key];
  for (let d=0; f && d<3; d++, f=f.return) for (const cp of [f, f.alternate]) { let h=cp&&cp.memoizedState;
    for (let i=0; h && typeof h==='object' && 'next' in h && i<200; i++, h=h.next) { const st=h.memoizedState; const v=Array.isArray(st)?st[0]:st;
      if (v && typeof v.render==='function' && typeof v.loadPreset==='function') return v.__revampFrameBudget || 0 } } } return null})()"""


def _tone(path):
    # Sweeping tones: the spectrum changes every frame.
    with wave.open(str(path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * (math.sin(2 * math.pi * (200 + 150 * math.sin(i / 4000)) * i / 22050)
                                                             + math.sin(2 * math.pi * 1500 * i / 22050) * math.sin(i / 3000))))
                               for i in range(22050 * 30)))


@pytest.mark.skipif(not CHROME, reason="needs Chrome")
def test_spectrum_animates_before_during_and_after_milkdrop(tmp_path):
    lib, data = tmp_path / "lib", tmp_path / "data"; lib.mkdir(); data.mkdir()
    _tone(lib / "tone.wav")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = subprocess.Popen([sys.executable, "-m", "revamp_core.server", "--port", str(port), "--data", str(data), "--library", str(lib)],
                           cwd=str(ROOT), env={**os.environ, "REVAMP_HOME": str(data), "REVAMP_PORT": str(port)},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp(dir=str(tmp_path)); ch = None
    try:
        for _ in range(60):
            try: urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2); break
            except Exception: time.sleep(0.3)
        tok = (data / "token").read_text().strip()
        def api(path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                         headers={"X-Revamp-Token": tok, "Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=5))
        tid = None
        for _ in range(60):
            rows = api("/api/library/tracks"); rows = rows.get("tracks") or rows.get("items") or []
            if rows: tid = rows[0]["id"]; break
            time.sleep(0.3)
        assert tid, "fixture track was not indexed"
        ch = subprocess.Popen([CHROME, "--headless=new", "--mute-audio", "--no-first-run", "--autoplay-policy=no-user-gesture-required",
                               "--use-gl=angle", "--use-angle=swiftshader-webgl", "--enable-unsafe-swiftshader",
                               f"--user-data-dir={prof}", "--remote-debugging-port=0", "--window-size=1100,800", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if os.path.exists(prof + "/DevToolsActivePort"): break
            time.sleep(0.2)
        dport = open(prof + "/DevToolsActivePort").read().split()[0]
        t = json.load(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{dport}/json/new?http://127.0.0.1:{port}/?mode=full", method="PUT")))
        ws = websocket.create_connection(t["webSocketDebuggerUrl"], suppress_origin=True)
        n = [0]
        def ev(expr):
            n[0] += 1
            ws.send(json.dumps({"id": n[0], "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == n[0]: return r.get("result", {}).get("result", {}).get("value")
        for _ in range(80):
            if ev("!!(window.revampPlayerApi && document.querySelector('#webamp canvas#visualizer'))"): break
            time.sleep(0.25)
        api("/api/command", {"action": "load", "items": [{"url": f"/api/media/{tid}", "track_id": tid}], "play": True})
        for _ in range(40):
            if ev("revampPlayerApi.webamp.store.getState().media.status") == "PLAYING": break
            time.sleep(0.25)
        def frames():
            seen = set()
            for _ in range(8):
                seen.add(ev(FRAME)); time.sleep(0.15)
            return len(seen)
        time.sleep(1)
        assert frames() > 2, "spectrum does not move while playing"
        # REVamp's own analyzer draws; Webamp's is hidden and no longer paints.
        assert ev("!!document.querySelector('.revamp-minivis')"), "REVamp analyzer not attached"
        assert ev("getComputedStyle(document.querySelector('#webamp canvas#visualizer')).opacity") == "0"  # still clickable
        assert ev("window.__revampVisFps") < 1
        assert ev("document.querySelector('#webamp canvas#visualizer').dataset.revampVis"), "saved analyzer look never applied"
        # Hidden by the host (Mini / parked): no frames at all; shown again: it resumes.
        ev("window.postMessage({type:'revamp:visible', visible:false}, '*')"); time.sleep(0.6)
        assert frames() == 1, "analyzer kept drawing while the player was hidden"
        ev("window.postMessage({type:'revamp:visible', visible:true}, '*')"); time.sleep(0.6)
        assert frames() > 2, "analyzer did not resume when shown"
        # A click on the analyzer still reaches Webamp and cycles to the oscilloscope.
        box = ev("(()=>{const r=document.querySelector('#webamp canvas#visualizer').getBoundingClientRect(); return [r.x+r.width/2, r.y+r.height/2]})()")
        for kind in ("mousePressed", "mouseReleased"):
            n[0] += 1
            ws.send(json.dumps({"id": n[0], "method": "Input.dispatchMouseEvent", "params": {"type": kind, "x": box[0], "y": box[1], "button": "left", "clickCount": 1}}))
            while json.loads(ws.recv()).get("id") != n[0]: pass
        time.sleep(0.5)
        assert ev("revampPlayerApi.webamp.store.getState().display.visualizerStyle") == 1, "click no longer cycles the analyzer"
        assert not ev("!!document.querySelector('.revamp-minivis')?.isConnected"), "REVamp bars stayed over the oscilloscope"
        if ev("revampPlayerApi.toggleVisualizer(true)"):
            time.sleep(2)
            assert frames() > 2, "spectrum froze while MilkDrop is open"
            # This browser renders WebGL in software (like Hermes' NVIDIA fallback), where
            # uncapped MilkDrop costs about a core: the player must give it a frame budget.
            assert ev(BUDGET) == 24, "MilkDrop runs uncapped under software WebGL (30 on a GPU)"
            ev("revampPlayerApi.toggleVisualizer(false)"); time.sleep(1)
            assert frames() > 2, "spectrum stayed frozen after MilkDrop closed"
        ws.close()
    finally:
        if ch: ch.terminate(); ch.wait()
        srv.terminate(); srv.wait()
        shutil.rmtree(prof, True)
