"""Contracts for the release gate scripts, kept executable without Hermes."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_gate_runs_every_required_check_from_disk_backed_tmpdir():
    src = (ROOT / "tools" / "release_gate.sh").read_text()
    assert 'scratch="${HOME}/.cache/revamp-gate"' in src
    assert 'export TMPDIR="$scratch/tmp"' in src
    for required in ("pytest", "node tests", "browser e2e", "Hermes manifest", "private-info scan", "version consistency"):
        assert required in src
    assert 'hermes plugins validate "$export_dir"' in src
    # The patterns themselves live outside the repo, so the gate never publishes what it hunts.
    assert "REVAMP_PRIVATE_PATTERNS" in src and ".config/revamp/private-patterns" in src
    assert "no private patterns file" in src, "a missing patterns file fails the gate"
    for leaked in ("callisto", "/home" "/dev", "/mnt" "/homes"):
        assert leaked not in src


def test_install_verifier_compares_health_version_build_and_listener_count():
    src = (ROOT / "tools" / "verify_install.sh").read_text()
    assert "/api/health" in src
    assert 'served_version' in src and 'served_build' in src
    assert 'ss -ltnp' in src
    assert 'test "$count" = 1' in src


def test_ci_has_cross_platform_node_and_ubuntu_browser_jobs():
    src = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
    assert "os: [ubuntu-latest, windows-latest, macos-latest]" in src
    assert "websocket-client" in src
    assert "google-chrome-stable" in src
    assert "node --test" in src
    assert "browser e2e (ubuntu)" in src
