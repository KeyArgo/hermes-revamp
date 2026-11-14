#!/usr/bin/env python3
"""release_check — keep the version, the changelog, the tag and the catalog pin telling one story.

Vendored into every InovinLabs plugin at ``tools/release_check.py`` (standard library only).

    python3 tools/release_check.py            # every push: the cheap invariants
    python3 tools/release_check.py --release  # before tagging, and on a tag push: the release itself
    python3 tools/release_check.py --notes    # print this version's CHANGELOG section (release body)

What it guards (each one went wrong at least once in the 1.0.0 releases):

* ``plugin.yaml`` ``version:`` is SemVer, and every file listed in ``.release-check.json`` ->
  ``mirrors`` repeats the same number (a second ``__version__`` that drifts is a second truth).
* A released version names exactly one commit: if ``v<version>`` already exists on another commit,
  the next release must bump ``version:`` first.
* ``--release``: ``CHANGELOG.md`` has a section headed with this exact version and a
  ``YYYY-MM-DD`` date; the heading never says "unreleased"; the section is not empty; HEAD, when
  tagged, is tagged ``v<version>``; the working tree is clean.

Release notes come from that section (``--notes``), so the GitHub release can never carry another
version's text.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
HEADING = re.compile(r"^##\s+\[?v?(?P<ver>\d+\.\d+\.\d+[0-9A-Za-z.+-]*)\]?(?P<rest>.*)$")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def git(*args: str) -> str:
    proc = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def manifest_version() -> str:
    for line in (ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def changelog_section(version: str) -> tuple[str, str] | None:
    """(heading line, body) of the section for exactly ``version``, or None."""
    path = ROOT / "CHANGELOG.md"
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        match = HEADING.match(line)
        if match and match.group("ver") == version:
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                body.append(nxt)
            return line, "\n".join(body).strip()
    return None


def mirrors() -> list[str]:
    cfg = ROOT / ".release-check.json"
    if not cfg.is_file():
        return []
    return list(json.loads(cfg.read_text(encoding="utf-8")).get("mirrors", []))


def check(release: bool) -> list[str]:
    errors: list[str] = []
    version = manifest_version()
    if not SEMVER.match(version or ""):
        return [f"plugin.yaml version {version!r} is not SemVer"]
    if "-" in version:
        errors.append(f"plugin.yaml version {version!r} carries a pre-release tag: the file states the "
                      "release being worked toward; '-dev.N' is computed at runtime, never written")
    cfg = ROOT / ".release-check.json"
    if cfg.is_file():
        base = json.loads(cfg.read_text(encoding="utf-8")).get("dev_base", 0)
        if type(base) is not int or base < 0:
            errors.append(f".release-check.json dev_base {base!r} must be a whole number >= 0")

    for rel in mirrors():
        text = (ROOT / rel).read_text(encoding="utf-8") if (ROOT / rel).is_file() else ""
        if rel.endswith((".yaml", ".yml")):
            pattern = r"""^version:\s*["']?%s["']?\s*$"""
        elif rel.endswith(".toml"):
            pattern = r"""^version\s*=\s*["']%s["']"""
        elif rel.endswith(".json"):
            pattern = r'"version"\s*:\s*"%s"'
        else:
            pattern = r"""(?:__version__|VERSION)\s*=\s*["']%s["']"""
        if not re.search(pattern % re.escape(version), text, re.M):
            errors.append(f"{rel} does not declare version {version} (it must mirror plugin.yaml)")

    if not (ROOT / "CHANGELOG.md").is_file():
        errors.append("CHANGELOG.md is missing")

    head = git("rev-parse", "HEAD")
    tagged = git("rev-list", "-n", "1", f"v{version}")
    if tagged and head and tagged != head and release:
        errors.append(f"v{version} is already released at {tagged[:8]}; bump plugin.yaml before releasing again")

    if release:
        if "-" in version:
            errors.append(f"{version} is a pre-release; a release has no '-' suffix")
        section = changelog_section(version)
        if section is None:
            errors.append(f"CHANGELOG.md has no '## {version} — YYYY-MM-DD' section")
        else:
            heading, body = section
            if "unreleased" in heading.lower():
                errors.append(f"CHANGELOG heading still says unreleased: {heading!r}")
            if not DATE.search(heading):
                errors.append(f"CHANGELOG heading has no YYYY-MM-DD date: {heading!r}")
            if not body:
                errors.append(f"CHANGELOG section for {version} is empty")
        head_tags = git("tag", "--points-at", "HEAD").split()
        version_tags = [t for t in head_tags if re.match(r"^v\d+\.\d+\.\d+", t)]
        if version_tags and f"v{version}" not in version_tags:
            errors.append(f"HEAD is tagged {version_tags} but plugin.yaml says {version}")
        stale = []
        for rel in git("ls-files", "*.md").splitlines():
            if rel == "CHANGELOG.md" or rel.startswith(("docs/history/", "third_party/", "vendor/")):
                continue
            for line in (ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines():
                if "Applies to" in line:
                    found = re.findall(r"\b(\d+\.\d+\.\d+)\b", line)
                    if found and version not in found:
                        stale.append(f"{rel} ({found[0]})")
                    break
        if stale:
            errors.append(f"docs still say they apply to another version: {', '.join(stale[:8])}")
        if git("status", "--porcelain", "--untracked-files=no"):
            errors.append("the working tree has uncommitted changes")
    elif tagged and head and tagged != head:
        print(f"note: v{version} was released at {tagged[:8]}; this commit is past it. "
              f"Bump plugin.yaml before the next release or catalog pin.", file=sys.stderr)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", action="store_true", help="check a release (before tagging, or on a tag)")
    parser.add_argument("--notes", action="store_true", help="print this version's CHANGELOG section")
    args = parser.parse_args()
    if args.notes:
        section = changelog_section(manifest_version())
        if section is None:
            print("no CHANGELOG section for this version", file=sys.stderr)
            return 1
        print(section[1])
        return 0
    errors = check(args.release)
    for err in errors:
        print(f"release_check: {err}", file=sys.stderr)
    if not errors:
        print(f"release_check: ok ({manifest_version()}{', release' if args.release else ''})")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
