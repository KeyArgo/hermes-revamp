"""Hermes runs the player in a sandboxed iframe (no allow-same-origin, so origin "null").

Regression: dropping CORS left the module script blocked there; the page showed bare
HTML and nothing played, while every same-origin test still passed.
"""
import http.server, json, os, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path
import pytest

websocket = pytest.importorskip("websocket")
CHROME = next((p for p in (os.environ.get("CHROME_BIN") and shutil.which(os.environ["CHROME_BIN"]) or "",
                           "/opt/google/chrome/chrome", shutil.which("google-chrome") or "",
                           shutil.which("chromium") or "") if p and os.path.exists(p)), None)
ROOT = Path(__file__).resolve().parents[1]
# Same sandbox flags as desktop/plugin.js uses for both player iframes.
SANDBOX = "allow-scripts allow-forms allow-downloads"


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.mark.skipif(not CHROME, reason="needs Chrome")
@pytest.mark.parametrize("query", ["?embed=1", "?embed=1&control=1"])
def test_player_boots_inside_hermes_sandboxed_iframe(tmp_path, query):
    data = str(tmp_path / "data"); os.makedirs(data)
    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "revamp_core.server", "--port", str(port), "--data", data],
                           cwd=str(ROOT), env={**os.environ, "REVAMP_HOME": data, "REVAMP_PORT": str(port)},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # The host page lives on a different origin, like the Hermes Desktop renderer.
    host_dir = tmp_path / "host"; host_dir.mkdir()
    (host_dir / "index.html").write_text(
        f'<!doctype html><iframe id=f sandbox="{SANDBOX}" allow="autoplay; fullscreen" '
        f'style="width:900px;height:700px" src="http://127.0.0.1:{port}/{query}"></iframe>'
        "<script>window.msgs=[];addEventListener('message',e=>msgs.push(e.data&&e.data.type||e.data))</script>")
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(host_dir), **k)
    host = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=host.serve_forever, daemon=True).start()
    prof = tempfile.mkdtemp(dir=str(tmp_path))
    ch = None
    try:
        def state():
            tok = open(os.path.join(data, "token")).read().strip()
            r = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-Revamp-Token": tok})
            return json.load(urllib.request.urlopen(r, timeout=5))
        for _ in range(60):
            try: state(); break
            except Exception: time.sleep(0.3)
        ch = subprocess.Popen([CHROME, "--headless=new", "--mute-audio", "--no-first-run", f"--user-data-dir={prof}",
                               "--remote-debugging-port=0", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if os.path.exists(prof + "/DevToolsActivePort"): break
            time.sleep(0.2)
        dport = open(prof + "/DevToolsActivePort").read().split()[0]
        t = json.load(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{dport}/json/new?http://127.0.0.1:{host.server_address[1]}/", method="PUT")))
        ws = websocket.create_connection(t["webSocketDebuggerUrl"], suppress_origin=True)
        msgs, connected = [], False
        for i in range(80):
            ws.send(json.dumps({"id": i + 1, "method": "Runtime.evaluate",
                                "params": {"expression": "window.msgs||[]", "returnByValue": True}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == i + 1: break
            msgs = r.get("result", {}).get("result", {}).get("value") or []
            if query == "?embed=1":
                connected = bool((state().get("live") or {}).get("player_connected"))
                if connected and "revamp:ready" in msgs: break
            elif msgs: break
            time.sleep(0.25)
        ws.close()
        # The page booted: its script ran and talked to the Hermes host page.
        assert msgs, f"sandboxed {query} page never ran its script (CORS?)"
        if query == "?embed=1":
            assert connected, "sandboxed player never connected to the server"
            assert "revamp:ready" in msgs, msgs
    finally:
        if ch: ch.terminate(); ch.wait()
        srv.terminate(); srv.wait(); host.shutdown()
        shutil.rmtree(prof, True)


@pytest.mark.skipif(not CHROME, reason="needs Chrome")
@pytest.mark.parametrize("query", ["?embed=1", "?embed=1&control=1", "?mode=full"])
def test_player_page_throws_no_uncaught_errors(tmp_path, query):
    # Regression: a renamed helper (paintViz) was still called from a 250 ms tick, so the
    # page threw four times a second and the code after it (lyrics follow) never ran.
    data = str(tmp_path / "data"); os.makedirs(data)
    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "revamp_core.server", "--port", str(port), "--data", data],
                           cwd=str(ROOT), env={**os.environ, "REVAMP_HOME": data, "REVAMP_PORT": str(port)},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp(dir=str(tmp_path))
    ch = None
    try:
        for _ in range(60):
            try: urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2); break
            except Exception: time.sleep(0.3)
        ch = subprocess.Popen([CHROME, "--headless=new", "--mute-audio", "--no-first-run", f"--user-data-dir={prof}",
                               "--remote-debugging-port=0", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if os.path.exists(prof + "/DevToolsActivePort"): break
            time.sleep(0.2)
        dport = open(prof + "/DevToolsActivePort").read().split()[0]
        t = json.load(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{dport}/json/new?about:blank", method="PUT")))
        ws = websocket.create_connection(t["webSocketDebuggerUrl"], suppress_origin=True)
        ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": f"http://127.0.0.1:{port}/{query}"}}))
        errors, deadline = [], time.time() + 6  # long enough for many 250 ms ticks
        ws.settimeout(0.5)
        while time.time() < deadline:
            try: msg = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException: continue
            if msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                errors.append((d.get("exception") or {}).get("description") or d.get("text"))
        ws.close()
        assert not errors, f"{query}: uncaught errors: {errors[:3]}"
    finally:
        if ch: ch.terminate(); ch.wait()
        srv.terminate(); srv.wait()
        shutil.rmtree(prof, True)
