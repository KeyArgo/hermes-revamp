"""Run the node tests that lock placement names and desktop-window geometry."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_node_placement_and_float_layout():
    subprocess.run(
        [NODE, "--test", "tests/placement_modes.test.mjs", "tests/float_layout.test.mjs"],
        cwd=REPO, check=True,
    )
