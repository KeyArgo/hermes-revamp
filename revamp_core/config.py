"""Revamp configuration.

Settings resolution order per key:

1. Hermes config: ``plugins.entries.revamp.<key>`` (only if the hermes_cli
   config module imports cleanly — the core must stay stdlib-only).
2. Environment variable ``REVAMP_<KEY>``.
3. Default.

The data dir is ``$REVAMP_HOME`` else ``$HERMES_HOME/revamp`` else
``~/.hermes/revamp``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

DEFAULT_PORT = 8114

#: name -> (env var, default); types are coerced by the accessor helpers.
_KEYS: dict[str, tuple[str, Any]] = {
    "port": ("REVAMP_PORT", DEFAULT_PORT),
    "library_dirs": ("REVAMP_LIBRARY_DIRS", ["~/Music"]),
    "duck_on_approval": ("REVAMP_DUCK_ON_APPROVAL", False),
    "duck_level": ("REVAMP_DUCK_LEVEL", 0.25),
}


# The Hermes profile home this Revamp belongs to, fixed when the plugin loads. One Hermes gateway
# may serve many profiles (multiplexing): then HERMES_HOME in the environment is the gateway's
# own (default) home, and only the plugin's load scope knows the profile. Server threads run
# outside that scope, so the home is pinned once and used from then on.
_PINNED_HOME: Optional[Path] = None


def current_hermes_home() -> Optional[str]:
    """The profile home of the calling context: Hermes's own resolver when available (it follows
    the profile scope under multiplexing), else HERMES_HOME."""
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:
        return os.environ.get("HERMES_HOME")


def pin_hermes_home(home: Optional[str]) -> None:
    """Fix the profile home for this process's Revamp (first pin wins)."""
    global _PINNED_HOME
    if home and _PINNED_HOME is None:
        _PINNED_HOME = Path(home).expanduser()


def data_dir() -> Path:
    """Resolve (and create) the Revamp data dir: $REVAMP_HOME, else <profile home>/revamp."""
    raw = os.environ.get("REVAMP_HOME")
    if not raw:
        hermes = str(_PINNED_HOME) if _PINNED_HOME is not None else os.environ.get("HERMES_HOME")
        raw = os.path.join(hermes, "revamp") if hermes else "~/.hermes/revamp"
    d = Path(raw).expanduser()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _hermes_setting(key: str) -> Optional[Any]:
    """Read ``plugins.entries.revamp.<key>`` from the Hermes config, if available."""
    try:
        from hermes_cli.config import cfg_get, load_config

        return cfg_get(load_config(), "plugins", "entries", "revamp", key)
    except Exception:
        return None


def _env_setting(env: str) -> Optional[str]:
    return os.environ.get(env) or None


def _coerce(key: str, raw: Any) -> Any:
    """Coerce *raw* to the type of the default for *key*."""
    default = _KEYS[key][1]
    try:
        if key == "port":
            return max(1, min(65535, int(raw)))
        if key == "library_dirs":
            if isinstance(raw, str):
                parts = [p.strip() for p in raw.split(os.pathsep) if p.strip()]
            else:
                parts = [str(p) for p in raw]
            return parts or list(default)
        if key == "duck_on_approval":
            if isinstance(raw, str):
                return raw.strip().lower() in ("1", "true", "yes", "on")
            return bool(raw)
        if key == "duck_level":
            v = float(raw)
            return min(1.0, max(0.0, v))
    except (TypeError, ValueError):
        return default
    return raw


def setting(key: str) -> Any:
    """Resolve one setting through hermes config -> env -> default."""
    default = _KEYS[key][1]
    for source in (_hermes_setting(key), _env_setting(_KEYS[key][0])):
        if source is None:
            continue
        return _coerce(key, source)
    return _coerce(key, default)


def settings() -> dict[str, Any]:
    return {k: setting(k) for k in _KEYS}


# ---------------------------------------------------------------------------
# App-chosen settings (settings.json in the data dir)
# ---------------------------------------------------------------------------

_SETTINGS_NAME = "settings.json"


def _settings_path() -> Path:
    return data_dir() / _SETTINGS_NAME


def _load_settings() -> dict[str, Any]:
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(data: dict[str, Any]) -> None:
    """Atomic write. Raises OSError, so a failed save is never reported as saved."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_FOLDER_KINDS = ("music", "books")  # a folder is one or the other, never both


def _norm_kind(kind: Any) -> str:
    k = str(kind or "").strip().lower()
    return k if k in _FOLDER_KINDS else "music"


def library_folders() -> list[dict]:
    """Library folders with kinds: app choice (settings.json) else config/env/default.

    The old ``library_dirs`` key (and any other kind) reads as ``music``; an explicitly saved
    empty ``library_folders`` means no folders (it does not fall back)."""
    raw = _load_settings()
    if isinstance(raw.get("library_folders"), list):
        out: list[dict] = []
        for item in raw["library_folders"]:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                out.append({"path": item["path"], "kind": _norm_kind(item.get("kind"))})
        return out
    if isinstance(raw.get("library_dirs"), list):
        return [{"path": str(p), "kind": "music"} for p in raw["library_dirs"]]
    out = setting("library_dirs")
    return [{"path": str(p), "kind": "music"} for p in (out if isinstance(out, list) else [])]


def library_dirs() -> list[str]:
    """Library roots: app choice (settings.json) else config/env/default."""
    return [f["path"] for f in library_folders()]


def library_dirs_custom() -> bool:
    """True when the user picked library folders in the app (even an empty list)."""
    raw = _load_settings()
    return isinstance(raw.get("library_folders"), list) or isinstance(raw.get("library_dirs"), list)


def set_library_folders(folders: list[dict]) -> None:
    """Persist the app-chosen folders (even an empty list). Raises OSError."""
    data = _load_settings()
    data["library_folders"] = [
        {"path": str(f.get("path") or ""), "kind": _norm_kind(f.get("kind"))}
        for f in folders
    ]
    data.pop("library_dirs", None)
    _save_settings(data)


def set_library_dirs(dirs: list[str]) -> None:
    """Persist paths as Music folders. Raises OSError."""
    set_library_folders([{"path": d, "kind": "music"} for d in dirs])
