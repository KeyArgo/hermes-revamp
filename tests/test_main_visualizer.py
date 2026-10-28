"""Main-window analyzer: calm Webamp defaults, and a click's mode is merged into settings."""

from __future__ import annotations

import colorsys
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_main_visualizer_defaults_and_persistence():
    subprocess.run(
        [NODE, "--test", "tests/main_visualizer.test.mjs"],
        cwd=REPO, check=True,
    )


def _viscolor(path: Path) -> list[tuple[int, int, int]]:
    with zipfile.ZipFile(path) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith("viscolor.txt"))
        text = zf.read(name).decode("latin-1")
    colors = []
    for line in text.splitlines():
        line = line.split("//", 1)[0].strip().strip(",")
        if not line:
            continue
        parts = [int(p) for p in re.findall(r"\d+", line)[:3]]
        if len(parts) == 3:
            colors.append(tuple(parts))
    return colors


def _hue_span(colors: list[tuple[int, int, int]]) -> float:
    hues = [colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] for r, g, b in colors]
    return max(hues) - min(hues)


def test_revamp_skins_use_a_clean_spectrum_gradient():
    # Bars are palette entries 2..17. A noisy rainbow swings hue by a large slice of the wheel.
    for name in ("REVamp-Nous.wsz", "REVamp-Nous-Dark.wsz"):
        colors = _viscolor(REPO / "web" / "skins" / name)
        assert len(colors) == 24, name
        assert _hue_span(colors[2:18]) < 0.08, name


def test_classic_base_palette_is_still_webamps_rainbow():
    # Noted, not regenerated: the base skin's colors live in the Webamp bundle, not a VISCOLOR.txt.
    bundle = (REPO / "web" / "vendor" / "webamp.butterchurn-bundle.min.mjs").read_text(encoding="utf-8")
    match = re.search(r'colors:\["rgb\(0,0,0\)"(?:,"rgb\(\d+,\d+,\d+\)"){23}\]', bundle)
    assert match, "built-in classic VISCOLOR was not found"
    colors = [tuple(int(n) for n in rgb) for rgb in re.findall(r"rgb\((\d+),(\d+),(\d+)\)", match.group(0))]
    assert len(colors) == 24
    assert _hue_span(colors[2:18]) > 0.25
