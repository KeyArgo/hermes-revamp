"""Library (control) mode: pressing Play in Music starts the ONE live player (regression: it did nothing)."""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
import pytest

websocket = pytest.importorskip("websocket")
CHROME = next((p for p in ("/opt/google/chrome/chrome", shutil.which("google-chrome") or "", shutil.which("chromium") or "") if p and os.path.exists(p)), None)
ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lanes" / "_e2e" / "lib"


@pytest.mark.skipif(not CHROME or not LIB.is_dir(), reason="needs Chrome and the e2e fixture library")
def test_library_mode_play_starts_live_player(tmp_path):
    root, lib, S = str(ROOT), str(LIB), str(tmp_path)
    data = tempfile.mkdtemp(dir=S); import socket as _s; _k=_s.socket(); _k.bind(('127.0.0.1',0)); port=_k.getsockname()[1]; _k.close()
    srv = subprocess.Popen([sys.executable, "-m", "revamp_core.server", "--port", str(port), "--data", data, "--library", lib], cwd=root, env={**os.environ, "REVAMP_HOME": data, "REVAMP_PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def get(p, tok=None):
        r = urllib.request.Request(f"http://127.0.0.1:{port}{p}", headers={"X-Revamp-Token": tok} if tok else {})
        return json.load(urllib.request.urlopen(r, timeout=5))
    for _ in range(60):
        try: get("/api/health"); break
        except Exception: time.sleep(0.3)
    tok = open(os.path.join(data, "token")).read().strip()
    for _ in range(60):
        if not get("/api/library/status", tok).get("scanning"): break
        time.sleep(0.5)
    prof = tempfile.mkdtemp(dir=S)
    ch = None
    try:
        ch = subprocess.Popen([CHROME, "--headless=new", "--mute-audio", "--autoplay-policy=no-user-gesture-required", f"--user-data-dir={prof}", "--remote-debugging-port=0", "--remote-allow-origins=*", "--window-size=1200,900", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3); dport = open(prof + "/DevToolsActivePort").read().split()[0]
        def tab(url):
            t = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{dport}/json/new?{url}", method="PUT")))
            ws = websocket.create_connection(t["webSocketDebuggerUrl"]); return ws
        n = [0]
        def ev(ws, expr):
            n[0] += 1; ws.send(json.dumps({"id": n[0], "method": "Runtime.evaluate", "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == n[0]: return r.get("result", {}).get("result", {}).get("value")
        player = tab(f"http://127.0.0.1:{port}/?mode=full")
        for _ in range(80):  # wait for the real player to connect
            if (get("/api/state", tok).get("live") or {}).get("player_connected"): break
            time.sleep(0.25)
        control = tab(f"http://127.0.0.1:{port}/?control=1")
        for _ in range(80):  # wait for the library rows to render
            if ev(control, "!!document.getElementById('lib-play') && document.querySelectorAll('tbody tr').length > 0"): break
            time.sleep(0.25)
        assert ev(control, "!!(window.revampPlayerApi && window.revampPlayerApi.control)")
        # click the first track row's play path: use the Play button with nothing selected (plays all shown)
        assert ev(control, "(()=>{const b=document.getElementById('lib-play'); if(!b) return 'no button'; b.click(); return 'ok'})()") == "ok"
        status = None
        for _ in range(60):  # poll: the full suite runs under load
            st = get("/api/state", tok); np = (st.get("live") or {}).get("now_playing") or {}
            status = np.get("status")
            if status == "PLAYING": break
            time.sleep(0.25)
    finally:
        if ch: ch.terminate(); ch.wait()
        srv.terminate(); srv.wait(); shutil.rmtree(prof, True); shutil.rmtree(data, True)
    assert status == "PLAYING", f"library-mode Play did not start the live player: {status}"
