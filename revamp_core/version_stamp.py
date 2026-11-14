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

Release or dev (VERSIONING-STANDARD.md §1): a build reads the bare ``X.Y.Z`` only when it is
that release — HEAD is exactly the ``vX.Y.Z`` tag, or Hermes installed this exact commit from
the plugin catalog (its install record carries the catalog pin). Anything else reads
``X.Y.Z-dev.N`` (``N`` = commit count; plain ``-dev`` in a shallow clone, where the count is
meaningless). A catalog install has no tags, so the install record is what proves it is the
reviewed release; ``release_check.py`` makes sure only a tagged release commit is ever pinned.

Usage in a plugin:

    from .version_stamp import describe, as_dict

    @plugin.command("version")
    def version_command(ctx):
        return describe()            # "1.0.0 · bfeb655 · 2026-11-05"

    def health():
        return as_dict()             # {"version": ..., "commit": ..., "dirty": ...}
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _plugin_root() -> Path:
    """The directory holding ``plugin.yaml``: this one, or up to two levels above it."""
    for candidate in (_HERE, _HERE.parent, _HERE.parent.parent):
        if (candidate / "plugin.yaml").is_file():
            return candidate
    return _HERE.parent


def _find_build_info() -> dict:
    """BUILD_INFO.json beside this file, or at the plugin root."""
    for candidate in (_HERE / "BUILD_INFO.json", _HERE.parent / "BUILD_INFO.json",
                      _plugin_root() / "BUILD_INFO.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                continue
    return {}


def _git(*args: str) -> str:
    for root in (_plugin_root(), _HERE.parent, _HERE):
        try:
            proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                  text=True, timeout=5, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
    return ""


def _declared_version() -> str:
    candidate = _plugin_root() / "plugin.yaml"
    try:
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _catalog_pin() -> str:
    """The commit Hermes installed this plugin at from the catalog, from its install record.

    Hermes keeps one ``.install-metadata.json`` in the plugins directory, keyed by the plugin's
    folder name. The package may sit a level or two below that folder, so walk up to find it."""
    for folder in (_HERE, *list(_HERE.parents)[:3]):
        try:
            records = json.loads((folder.parent / ".install-metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record = records.get(folder.name) if isinstance(records, dict) else None
        if isinstance(record, dict):
            block = record.get("catalog") or {}
            return str(block.get("pin") or block.get("sha") or "")
    return ""


def _is_release(declared: str, commit: str) -> bool:
    """True when this build is exactly release ``declared``: tagged, or installed from the catalog pin."""
    tag = _git("describe", "--tags", "--exact-match", "HEAD")
    if tag and tag.lstrip("v") == declared:
        return True
    pin = _catalog_pin()
    return bool(pin) and bool(commit) and pin == commit


def _dev_base() -> int:
    """Builds made before this repo's history began (``dev_base`` in ``.release-check.json``).

    A public repo can start from a fresh history while its private dev repo already numbered builds
    in the hundreds. Without the base, the count restarted near 1 and a new build read older than
    one already installed. N = dev_base + commits, so it only ever climbs.
    """
    for candidate in (_HERE, _HERE.parent, _HERE.parent.parent, _HERE.parent.parent.parent):
        path = candidate / ".release-check.json"
        if path.is_file():
            try:
                base = json.loads(path.read_text(encoding="utf-8")).get("dev_base", 0)
            except (OSError, ValueError, AttributeError):
                return 0
            return base if type(base) is int and base >= 0 else 0
    return 0


def _dev_suffix() -> str:
    if _git("rev-parse", "--is-shallow-repository") == "true":
        return "-dev"
    count = _git("rev-list", "--count", "HEAD")
    return f"-dev.{_dev_base() + int(count)}" if count.isdigit() else "-dev"


def as_dict() -> dict:
    """Everything a status line, a bug report or a support question needs."""
    build = _find_build_info()
    live = _git("rev-parse", "HEAD")
    if live and build.get("commit") and live != build["commit"]:
        # BUILD_INFO.json is a snapshot; in a checkout it goes stale at the next commit. Mixing it
        # with live git printed a dev count beside a hash two commits old, so git wins for every field.
        build = {}
    commit = build.get("commit") or live
    dirty = build.get("dirty")
    if dirty is None:
        status = _git("status", "--porcelain")
        dirty = bool(status)
    release = build.get("release") or _declared_version() or "unknown"
    version = build.get("version") or ""
    if not version:
        version = release
        # Without git or a commit there is nothing to compare, so the manifest is all we can say.
        if release != "unknown" and "-" not in release and commit and not _is_release(release, commit):
            version = release + _dev_suffix()
    return {
        "version": version,
        "release": release,
        "commit": commit or "",
        "commit_short": (commit or "")[:8],
        "commit_date": build.get("commit_date") or _git("log", "-1", "--format=%cI"),
        "built_at": build.get("built_at", ""),
        "channel": build.get("channel") or ("dev" if "-dev" in version else "release"),
        "dirty": bool(dirty),
        "source": "build_info" if build else ("git" if commit else "manifest"),
    }


def version() -> str:
    return as_dict()["version"]


def commit(short: bool = True) -> str:
    info = as_dict()
    return info["commit_short"] if short else info["commit"]


def describe() -> str:
    """"1.0.0 · bfeb655 · 2026-11-05" — one line, safe to show a user."""
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
