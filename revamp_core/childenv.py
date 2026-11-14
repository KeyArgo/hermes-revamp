"""The environment REVamp hands to the programs it starts (ffprobe, git, ps, the pop-out browser).

Hermes runs with provider keys and agent settings in its environment (OPENAI_API_KEY,
ANTHROPIC_API_KEY, HERMES_YOLO_MODE...). A child started without ``env=`` inherits all of them, so
every key reached ffprobe and Chrome. Children get an allowlist instead: what a tool or a desktop
window needs to find itself, its locale, its temp folder, the display and the sound server.
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, Mapping, Optional

# Exact names. Windows needs SYSTEMROOT (and friends) or a child can fail to start at all.
_NAMES = frozenset({
    "PATH", "PATHEXT", "HOME", "USER", "LOGNAME", "USERNAME", "USERPROFILE", "SHELL",
    "LANG", "LANGUAGE", "TZ", "TERM",
    "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "LOCALAPPDATA", "APPDATA",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
    "LD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR",
})
# Families a desktop window needs: locale, XDG folders and session, toolkits, Wayland, audio.
_PREFIXES = ("LC_", "XDG_", "WAYLAND_", "GTK_", "GDK_", "QT_", "PULSE_", "PIPEWIRE_")
# Never passed, whatever the allowlist says (a toolkit variable named like a secret is still one).
_SECRET_MARKS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "COOKIE", "AUTH_")


def _looks_secret(name: str) -> bool:
    upper = name.upper()
    if upper == "XAUTHORITY":           # the X display cookie file path: needed, not a secret value
        return False
    return any(mark in upper for mark in _SECRET_MARKS)


def child_env(extra: Optional[Mapping[str, str]] = None, *,
              keep: Iterable[str] = (), source: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """An allowlisted copy of the environment for a child process.

    ``keep`` adds exact names a particular child needs; ``extra`` sets values outright (it wins).
    """
    env_in = os.environ if source is None else source
    wanted = _NAMES | {k.upper() for k in keep}
    out: Dict[str, str] = {}
    for name, value in env_in.items():
        upper = name.upper()   # Windows names are case-insensitive ("Path", "SystemRoot")
        if (upper in wanted or upper.startswith(_PREFIXES)) and not _looks_secret(upper):
            out[name] = value
    if extra:
        out.update({str(k): str(v) for k, v in extra.items()})
    return out
