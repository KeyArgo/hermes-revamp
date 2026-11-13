"""version_stamp — drop this file into a plugin package so it can state its own identity.

Why it exists: a plugin's `version:` in ``plugin.yaml`` is a declaration, not a fact. Two
builds can carry the same version, a dev tree can be dirty, and an installed copy can be
weeks behind the source. This module answers the question a user actually has —
*"am I running the latest?"* — with the version **and** the commit **and** the build date.

Precedence, highest first:

1. ``BUILD_INFO.json`` written at build/install time (authoritative: it records the commit
   the artifact was built from, even when the installed copy has no ``.git``).
2. Live ``git`` in the surrounding repository (accurate on a development checkout).
3. ``plugin.yaml``'s ``version:`` (always available, no commit).
4. ``"unknown"``.

Usage in a plugin:

    from .version_stamp import describe, as_dict

    @plugin.command("version")
    def version_command(ctx):
        return describe()            # "1.0.0 · bfeb655 · 2026-11-05 (clean)"

    def health():
        return as_dict()             # {"version": ..., "commit": ..., "dirty": ...}
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _find_build_info() -> dict:
    """BUILD_INFO.json beside this file, or one directory up (plugin root)."""
    for candidate in (_HERE / "BUILD_INFO.json", _HERE.parent / "BUILD_INFO.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                continue
    return {}


def _git(*args: str) -> str:
    for root in (_HERE.parent, _HERE):
        try:
            proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                  text=True, timeout=5, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
    return ""


def _declared_version() -> str:
    for candidate in (_HERE.parent / "plugin.yaml", _HERE / "plugin.yaml"):
        if not candidate.is_file():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def as_dict() -> dict:
    """Everything a status line, a bug report or a support question needs."""
    build = _find_build_info()
    commit = build.get("commit") or _git("rev-parse", "HEAD")
    dirty = build.get("dirty")
    if dirty is None:
        status = _git("status", "--porcelain")
        dirty = bool(status)
    return {
        "version": build.get("version") or _declared_version() or "unknown",
        "commit": commit or "",
        "commit_short": (commit or "")[:8],
        "commit_date": build.get("commit_date") or _git("log", "-1", "--format=%cI"),
        "built_at": build.get("built_at", ""),
        "channel": build.get("channel") or ("dev" if "-dev" in str(build.get("version", "")) else "release"),
        "dirty": bool(dirty),
        "source": "build_info" if build else ("git" if commit else "manifest"),
    }


def version() -> str:
    return as_dict()["version"]


def commit(short: bool = True) -> str:
    info = as_dict()
    return info["commit_short"] if short else info["commit"]


def describe() -> str:
    """"1.0.0 · bfeb655 · 2026-11-05 (clean)" — one line, safe to show a user."""
    info = as_dict()
    parts = [info["version"]]
    if info["commit"]:
        parts.append(info["commit"][:8])       # short, always, even with a full hash
    if info["commit_date"]:
        parts.append(info["commit_date"][:10])
    line = " · ".join(parts)
    return line + (" (modified)" if info["dirty"] else "")


if __name__ == "__main__":
    print(describe())
