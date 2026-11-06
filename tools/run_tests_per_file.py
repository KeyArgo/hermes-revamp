"""Run each tests/test_*.py in its own pytest process, as tools/release_gate.sh does.

Some tests start watcher threads and servers that live for the whole process. In one process for
the whole tree they can disturb tests in a later file (seen only on CI); one process per file
keeps every file's run the same as its own run. Usage:
    python tools/run_tests_per_file.py [--ignore tests/x.py ...]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ignore", action="append", default=[])
    args = ap.parse_args()
    skip = {Path(p).name for p in args.ignore}
    failed = []
    base = Path(tempfile.mkdtemp(prefix="revamp-tests-"))
    files = sorted(p for p in (ROOT / "tests").glob("test_*.py") if p.name not in skip)
    for f in files:
        print(f"== {f.name}", flush=True)
        rc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             f"--basetemp={base / f.stem}", str(f)], cwd=ROOT).returncode
        if rc not in (0, 5):  # 5: every test in the file was skipped or deselected
            failed.append(f.name)
    print(f"\n{len(files) - len(failed)}/{len(files)} test files passed")
    for name in failed:
        print(f"FAILED {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
