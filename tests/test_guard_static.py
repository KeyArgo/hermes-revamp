"""Guards on code patterns that caused real bugs, so they cannot quietly come back.

Each check scans the source, so a new file or a new handler is covered the day it is
written, not after the next bug hunt.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSER_SRC = sorted([*ROOT.glob("web/*.js"), *ROOT.glob("web/*.mjs"), *ROOT.glob("web/views/*.js"),
                      *ROOT.glob("web/focus/*.js"), ROOT / "desktop" / "plugin.js"])

# Interpolations in HTML templates that are known constants (icons, labels, CSS classes),
# never text from files, stations or the network. Anything else must go through esc().
SAFE_HTML_INTERPOLATIONS = {
    ("web/app.js", "v.icon"), ("web/app.js", "v.label"), ("web/app.js", "okCls"),
    ("web/views/agent.js", "label"),
    ("web/views/library.js", 'books ? "Search audiobooks…" : "Search music…"'),
    ("web/views/library.js", 'books ? "Search audiobooks" : "Search music"'),
}


def _rel(p):
    return p.relative_to(ROOT).as_posix()


def test_html_templates_only_interpolate_escaped_text():
    # Track titles, station names, skin names and error messages are untrusted: put into
    # innerHTML unescaped, they run as markup (XSS) inside the page that holds the token.
    bad = []
    for f in BROWSER_SRC:
        s = f.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:innerHTML\s*\+?=|insertAdjacentHTML\([^,]+,)\s*`", s):
            body = s[m.end():s.index("`", m.end())]
            for expr in re.findall(r"\$\{([^}]*)\}", body):
                if re.match(r"\s*(esc|escapeHtml|escHtml)\(", expr) or (_rel(f), expr.strip()) in SAFE_HTML_INTERPOLATIONS:
                    continue
                bad.append(f"{_rel(f)}:{s.count(chr(10), 0, m.start()) + 1}: ${{{expr}}}")
    assert not bad, "unescaped text in an HTML template (use esc() or textContent):\n" + "\n".join(bad)


def test_every_message_handler_checks_who_sent_it():
    # Any page can postMessage this window; each handler must first check the sender
    # (e.source / frame identity) before acting on the data.
    bad = []
    pat = re.compile(r"addEventListener\((?:window,\s*)?['\"]message['\"]\s*,\s*(async\s*)?\(?(\w+)?\)?\s*=>|addEventListener\(['\"]message['\"],\s*(\w+)\)")
    for f in BROWSER_SRC:
        s = f.read_text(encoding="utf-8")
        for m in pat.finditer(s):
            named = m.group(3)
            if named:  # handler passed by name: check its body
                fm = re.search(rf"function {named}\([^)]*\)\s*{{", s)
                window = s[fm.end():fm.end() + 400] if fm else ""
            else:
                window = s[m.end():m.end() + 400]
            if ".source" not in window:
                bad.append(f"{_rel(f)}:{s.count(chr(10), 0, m.start()) + 1}")
    assert not bad, "message handlers that don't check e.source first:\n" + "\n".join(bad)


def test_token_urls_are_built_by_the_same_host_helper():
    # "//other.host/x" starts with "/" too: attaching the token by a startsWith("/") check
    # sent it to that host. authedUrl() refuses other hosts; nothing should hand-roll ?t=.
    bad = []
    for f in BROWSER_SRC:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"[?&]t=\$\{|searchParams\.set\(\s*['\"]t['\"]", line) and "function authedUrl" not in line:
                bad.append(f"{_rel(f)}:{i}: {line.strip()[:100]}")
    allowed = [b for b in bad if not b.startswith("web/app.js")]
    assert not allowed, "token attached outside authedUrl():\n" + "\n".join(allowed)


def test_tests_never_run_against_the_real_home():
    # Tests once rewrote the user's ~/.local/share/applications launchers and wrote into
    # ~/.hermes/revamp. conftest gives every test a throwaway HOME.
    home = Path(os.environ["HOME"])
    assert "home-sandbox" in home.parts, f"HOME is not sandboxed: {home}"
    assert Path.home() == home
