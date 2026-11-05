"""Round 21 follow-ups outside the book path."""
import os
import subprocess
from pathlib import Path

import pytest

from revamp_core import proof

ROOT = Path(__file__).resolve().parents[1]


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self, *a):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_listener_answering_with_a_non_ascii_proof_is_not_ours(monkeypatch):
    # compare_digest raises TypeError on a non-ASCII str, which killed the caller instead of
    # answering "not us" (Claude + Grok a10r21).
    class Opener:
        def open(self, req, timeout=None):
            return _Resp('{"proof": "caf\u00e9"}'.encode())
    monkeypatch.setattr(proof, "local_opener", lambda: Opener())
    assert proof.verify_listener("http://127.0.0.1:9", "token") is False


def _gate_step(name):
    text = (ROOT / "tools/release_gate.sh").read_text()
    return text[text.index(f'run_shell "{name}"'):]


def test_the_version_check_fails_on_any_mismatch_not_only_the_last():
    step = _gate_step("version consistency")
    step = step[:step.index("if (( visual ))")]
    assert step.count("&&") >= 4 and ";" not in step.replace(";;", "")


def test_the_private_scan_covers_hidden_and_ignored_files():
    step = _gate_step("private-info scan")
    assert "--hidden" in step[:step.index("version consistency")] and "--no-ignore" in step[:step.index("version consistency")]


def test_the_browser_step_fails_when_the_browser_tests_cannot_run():
    step = _gate_step("browser e2e")
    assert "websocket" in step[:300] and "chrome" in step[:300].lower()


def test_the_identity_check_also_treats_a_non_ascii_proof_as_not_ours(monkeypatch):
    class Opener:
        def open(self, req, timeout=None):
            return _Resp('{"proof": "café"}'.encode())
    monkeypatch.setattr(proof, "local_opener", lambda: Opener())
    assert proof.listener_identity("http://127.0.0.1:9", "token") is False


@pytest.mark.skipif(os.name == "nt", reason="the gate is a bash script, run on Linux")
def test_the_version_check_passes_on_this_tree():
    # With "&&" the page check finally counts, and it looked for "alpha.10" where the page says
    # "1.0 alpha 10": the stricter gate failed on a correct tree (round 22 gate).
    step = _gate_step("version consistency")
    body = step[step.index("'") + 1:step.index("if (( visual ))")].rstrip().rstrip("'")
    assert subprocess.run(["bash", "-o", "pipefail", "-c", body], cwd=ROOT).returncode == 0


def test_a_new_install_starts_on_the_revamp_nous_light_skin():
    # Owner 11-05: REVamp's own Nous (white) skin is the first look, not Winamp's Classic.
    from revamp_core import server, store
    want = store._defaults()["skin"]
    assert want == "builtin:REVamp-Nous.wsz"
    assert any(s["id"] == want and s["url"] for s in server.skins_list()), "the default skin must ship"
