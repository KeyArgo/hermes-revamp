"""Hermes plugin components never call a React hook after an early return (React error #310 crashed the page)."""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "desktop" / "plugin.js").read_text(encoding="utf-8")


def _components():
    # top-level `function Name(` components (capitalised), body up to the next top-level function
    for m in re.finditer(r"^function ([A-Z]\w*)\(.*?\{\n(.*?)(?=^function |\Z)", SRC, re.S | re.M):
        yield m.group(1), m.group(2)


def test_no_hook_after_an_early_return():
    """A component-level `if (...) return` (2-space indent) must come after all of its hooks."""
    bad = []
    for name, body in _components():
        returned = in_if = False
        for line in body.split("\n"):
            if line.startswith("  return"):
                break  # the component's own final return
            if line.startswith("  if ("):
                in_if = True
                if re.search(r"\)\s*return\b", line):
                    returned = True
            elif in_if and line.startswith("    return"):
                returned = True
            elif line.startswith("  }"):
                in_if = False
            if returned and re.match(r"^  (const|let) .*\buse[A-Z]\w*\(", line):
                bad.append(f"{name}: {line.strip()}")
    assert not bad, "hooks after an early return: " + "; ".join(bad)
