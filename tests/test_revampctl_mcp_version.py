"""The MCP handshake reports the version REVamp declares, not a stale alpha string."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _declared() -> str:
    for line in (REPO / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    raise AssertionError("plugin.yaml has no version")


def test_mcp_initialize_reports_the_plugin_version(tmp_path):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    env = dict(os.environ, REVAMP_HOME=str(tmp_path), REVAMP_PORT="9")  # no server needed
    out = subprocess.run([sys.executable, str(REPO / "bin" / "revampctl"), "mcp"],
                         input=json.dumps(msg) + "\n", capture_output=True, text=True,
                         timeout=30, env=env)
    reply = json.loads(out.stdout.splitlines()[0])
    assert reply["result"]["serverInfo"]["version"] == _declared()
    assert "alpha" not in (REPO / "bin" / "revampctl").read_text(encoding="utf-8")
