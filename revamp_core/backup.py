"""Private rolling backups of the user's REVamp data, outside ``~/.hermes``.

A Hermes reinstall, a deleted profile or a bad migration must never be able to
take the user's likes, queue, resume points, settings, radio favorites and
playlists with it. ``snapshot()`` copies the small state files into
``$REVAMP_BACKUP_DIR`` (default ``~/.local/share/revamp/backups/<profile>-<hash8>``)
as immutable, content-deduplicated dirs with a ``manifest.json``; ``restore``
and ``export_zip``/``import_zip`` put them back. Stdlib only.
"""

from __future__ import annotations

import hashlib
import io
import logging
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

#: Files under the data dir that get backed up (relpaths). ``library.json`` is
#: rebuildable and big, ``token`` is a secret, ``skins/`` is cache — none of
#: those belong in a private backup of user data.
BACKED_UP_FILES = ("state.json", "settings.json", "radio_favorites.json")

PLAYLIST_DIR = "playlists"
PLAYLIST_EXTS = (".m3u8", ".m3u", ".pls")

MAX_IMPORT_BYTES = 50 * 1024 * 1024  # total uncompressed size of an imported zip

MANIFEST_NAME = "manifest.json"

#: Serializes id choice + final rename inside snapshot(): two snapshots in the
#: same second must not pick the same id and fight over one os.rename.
_SNAP_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def _backed_up_relpaths(data_dir: Path) -> List[str]:
    """Every backed-up relpath that exists in *data_dir*, sorted, playlists first-class."""
    out = []
    for name in BACKED_UP_FILES:
        p = data_dir / name
        # Only regular files: a symlinked state.json could leak token
        # targets or point anywhere on disk.
        if p.is_file() and not p.is_symlink():
            out.append(name)
    try:
        entries = sorted(os.listdir(data_dir / PLAYLIST_DIR))
    except OSError:
        entries = []
    for name in entries:
        p = data_dir / PLAYLIST_DIR / name
        if name.lower().endswith(PLAYLIST_EXTS) and p.is_file() and not p.is_symlink():
            out.append(f"{PLAYLIST_DIR}/{name}")
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_root(data_dir) -> Path:
    """The backup root for *data_dir*: ``$REVAMP_BACKUP_DIR`` if set, else
    ``~/.local/share/revamp/backups/<parent-name>-<hash8 of the abs path>``. mode 0o700."""
    env = os.environ.get("REVAMP_BACKUP_DIR")
    if env:
        root = Path(env).expanduser()
    else:
        data_dir = Path(data_dir).expanduser().resolve()
        name = data_dir.parent.name or "data"
        digest = hashlib.sha256(str(data_dir).encode("utf-8")).hexdigest()[:8]
        root = Path("~/.local/share/revamp/backups").expanduser() / f"{name}-{digest}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


# ---------------------------------------------------------------------------
# Snapshot / list / prune
# ---------------------------------------------------------------------------

def _read_manifest(snapshot_dir: Path) -> Optional[Dict]:
    try:
        raw = json.loads((snapshot_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(raw, dict) and isinstance(raw.get("id"), str) and isinstance(raw.get("files"), dict)):
        return None
    # Trust the folder, not the manifest: a tampered "id" must not point
    # prune/restore outside the snapshot dir the manifest was read from.
    raw["id"] = snapshot_dir.name
    return raw


def _same_content(a: Path, b: Path) -> bool:
    try:
        return _sha256(a) == _sha256(b)
    except OSError:
        return False


def snapshot(data_dir, *, root=None, now=None) -> Optional[Dict]:
    """Copy the backed-up files into ``<root>/<YYYYmmdd-HHMMSS[-n]>/`` with a manifest.

    Returns the manifest, or None when there is nothing to back up or the
    content equals the newest snapshot. Atomic: built in a temp dir, renamed.
    Dirs 0o700, files 0o600."""
    data_dir = Path(data_dir)
    root = Path(root) if root is not None else backup_root(data_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
    except OSError:
        pass
    relpaths = _backed_up_relpaths(data_dir)
    current: Dict[str, str] = {}
    for rel in relpaths:
        try:
            current[rel] = _sha256(data_dir / rel)
        except OSError:
            continue
    if not current:
        return None

    # One lock from the dedupe check through the final rename: concurrent
    # snapshots in the same second must not pick the same id.
    with _SNAP_LOCK:
        existing = list_backups(root)
        if existing:
            # Dedupe only when the current set of files AND their hashes is exactly
            # the newest snapshot's: a deleted playlist must count as a change.
            if existing[0].get("files") == current:
                return None

        ts = float(now if now is not None else time.time())
        base = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
        snap_id = base
        n = 1
        while (root / snap_id).exists():
            snap_id = f"{base}-{n}"
            n += 1
        final = root / snap_id
        tmp = Path(tempfile.mkdtemp(dir=str(root), prefix=".backup-", suffix=".tmp"))
        try:
            os.chmod(tmp, 0o700)
            files: Dict[str, str] = {}
            for rel in sorted(current):
                dst = tmp / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(data_dir / rel, dst)
                os.chmod(dst, 0o600)
                # Hash the copy, not the source: a write between hashing and
                # copying would make the manifest lie about the saved bytes.
                files[rel] = _sha256(dst)
            for d in tmp.rglob("*"):
                if d.is_dir():
                    os.chmod(d, 0o700)
            manifest = {"id": snap_id, "created": ts, "source": str(data_dir.resolve()),
                        "files": files}
            mpath = tmp / MANIFEST_NAME
            mpath.write_text(json.dumps(manifest), encoding="utf-8")
            os.chmod(mpath, 0o600)
            os.rename(tmp, final)
            os.chmod(final, 0o700)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return manifest


def _seq(name: str) -> int:
    """The sequence number of an id with the ``YYYYmmdd-HHMMSS-n`` form, else 0."""
    head, sep, tail = name.rpartition("-")
    if not sep or not head or not tail.isdigit() or "-" not in head:
        return 0
    return int(tail)


def list_backups(root) -> List[Dict]:
    """The manifests under *root*, newest first; unreadable dirs are skipped."""
    root = Path(root)
    out: List[tuple] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    for name in entries:
        m = _read_manifest(root / name)
        if m is None:
            continue
        try:
            created = float(m.get("created"))
        except (TypeError, ValueError):
            created = 0.0
        out.append((created, _seq(name), name, m))
    out.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [m for _, _, _, m in out]


def prune(root, *, keep_days=14, keep_min=3, keep_max=None) -> int:
    """Delete snapshots older than *keep_days*, but always keep the newest
    *keep_min*. An optional *keep_max* limits only snapshots older than the
    retention window. Returns the number deleted."""
    backups = list_backups(root)  # newest first
    cutoff = time.time() - keep_days * 86400
    keep_set = set()
    for i, m in enumerate(backups):
        # The newest keep_min always stay; fresher than cutoff stays while
        # there is room under keep_max. Fresh snapshots are never capped: the
        # ten-minute snapshot cadence needs about 2,016 slots for 14 days.
        fresh = m.get("created", 0) >= cutoff
        if i < keep_min or (fresh and (keep_max is None or i < keep_max)):
            keep_set.add(m["id"])
    deleted = 0
    for m in backups:
        if m["id"] in keep_set:
            continue
        try:
            shutil.rmtree(root / m["id"])
            deleted += 1
        except OSError:
            continue
    return deleted


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def _atomic_copy(src: Path, dst: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".restore-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(src.read_bytes())
        os.chmod(tmp, 0o600)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _undo_point(data_dir: Path, rels) -> Dict[str, Optional[bytes]]:
    """Current bytes of every file a restore will replace (None: not there yet). These are
    small (state, settings, playlists), and a write that fails half-way puts them back."""
    saved: Dict[str, Optional[bytes]] = {}
    for rel in rels:
        try:
            saved[rel] = (data_dir / rel).read_bytes()
        except FileNotFoundError:
            saved[rel] = None
    return saved


def _roll_back(data_dir: Path, saved: Dict[str, Optional[bytes]]) -> None:
    for rel, payload in saved.items():
        dst = data_dir / rel
        try:
            if payload is None:
                dst.unlink(missing_ok=True)
            else:
                tmp = dst.with_name(f".rollback-{dst.name}.tmp")
                tmp.write_bytes(payload)
                os.replace(tmp, dst)
        except OSError:
            logger.warning("revamp: could not roll back %s", rel)


def restore(backup_id, data_dir, *, root=None) -> List[str]:
    """Put the backup's files back (each via a temp file + ``os.replace``).

    The CURRENT data is snapshotted first, so a restore can be undone.
    ``playlists/`` ends up with exactly the backup's playlist files.
    Returns the restored relpaths. Unknown id -> KeyError."""
    data_dir = Path(data_dir)
    root = Path(root) if root is not None else backup_root(data_dir)
    manifest = None
    for m in list_backups(root):
        if m["id"] == backup_id:
            manifest = m
            break
    if manifest is None:
        raise KeyError(backup_id)
    files = dict(manifest.get("files") or {})
    # Validate every relpath before anything is written: a tampered manifest
    # must not be able to escape the data dir (or silently skip a file).
    src_dir = root / manifest["id"]
    for rel, digest in files.items():
        if not _valid_relpath(rel):
            raise ValueError(f"invalid relpath in backup: {rel!r}")
        # A damaged backup (missing, replaced or altered file) is refused whole,
        # before anything is written, rather than half-restored.
        src = src_dir / rel
        if src.is_symlink() or not src.is_file() or _sha256(src) != digest:
            raise ValueError(f"backup {manifest['id']} is damaged: {rel}")
    snapshot(data_dir, root=root)
    saved = _undo_point(data_dir, files)
    try:
        for rel in sorted(files):
            src = src_dir / rel
            dst = data_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _atomic_copy(src, dst)
    except OSError:
        # Half old, half new is worse than either: put the current files back.
        _roll_back(data_dir, saved)
        raise
    # Playlists mirror the backup exactly: drop ones the backup lacks.
    keep = {rel.split("/", 1)[1] for rel in files if rel.startswith(PLAYLIST_DIR + "/")}
    try:
        existing = os.listdir(data_dir / PLAYLIST_DIR)
    except OSError:
        existing = []
    for name in existing:
        if name in keep or not name.lower().endswith(PLAYLIST_EXTS):
            continue
        try:
            (data_dir / PLAYLIST_DIR / name).unlink()
        except OSError:
            pass
    return sorted(files)


# ---------------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------------

def export_zip(data_dir) -> bytes:
    """A zip of the current backed-up files plus ``manifest.json``."""
    data_dir = Path(data_dir)
    out = io.BytesIO()
    relpaths = _backed_up_relpaths(data_dir)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        files = {}
        for rel in relpaths:
            # Read once: the zip entry and its manifest hash describe the same bytes,
            # even if the player saves this file mid-export.
            try:
                payload = (data_dir / rel).read_bytes()
            except OSError:
                continue
            z.writestr(rel, payload)
            files[rel] = hashlib.sha256(payload).hexdigest()
        manifest = {"created": time.time(), "source": str(data_dir.resolve()), "files": files}
        z.writestr(MANIFEST_NAME, json.dumps(manifest))
    return out.getvalue()


def _valid_relpath(rel: str) -> bool:
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return False
    if rel in BACKED_UP_FILES or rel == MANIFEST_NAME:
        return True
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return parts[0] == PLAYLIST_DIR and len(parts) == 2 and rel.lower().endswith(PLAYLIST_EXTS)


def _valid_entry_name(rel: str) -> bool:
    """Every ZipInfo name must pass this, directory entries included: a
    directory entry is only allowed if it is exactly ``playlists/``."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = rel.split("/")
    if "" in parts[:-1] or ".." in parts:
        return False
    if rel.endswith("/"):
        return rel == PLAYLIST_DIR + "/"
    return _valid_relpath(rel)


def _entry_is_symlink(info) -> bool:
    mode = info.external_attr >> 16
    return bool(mode) and stat.S_ISLNK(mode)


def import_zip(data: bytes, data_dir, *, root=None) -> List[str]:
    """Write the zip's backed-up files into *data_dir*.

    Every entry is validated first (allowed relpaths only, no absolute paths,
    no ``..``, no symlinks, <= 50 MB uncompressed total): anything invalid
    raises ValueError and NOTHING is written. The current data is snapshotted
    first, so the import can be undone. Returns the imported relpaths."""
    data = bytes(data)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a zip: {exc}") from exc
    # First pass: validate names and declared sizes from the central directory
    # only, so a zip bomb is refused before anything is decompressed.
    declared = 0
    names: set = set()
    for info in zf.infolist():
        if not _valid_entry_name(info.filename):
            raise ValueError(f"entry not allowed: {info.filename!r}")
        # Two entries with one name: reads go by name, so the checks and the write could
        # see different entries. A real export never has duplicates.
        if info.filename in names:
            raise ValueError(f"duplicate entry in the zip: {info.filename!r}")
        names.add(info.filename)
        if _entry_is_symlink(info):
            raise ValueError(f"symlink entries are not allowed: {info.filename!r}")
        try:
            declared += int(info.file_size)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bad size in {info.filename!r}") from exc
        if declared > MAX_IMPORT_BYTES:
            raise ValueError("zip unpacks to more than 50 MB")
    payloads: Dict[str, bytes] = {}
    declared_files = None
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        if not _valid_relpath(info.filename):
            raise ValueError(f"entry not allowed: {info.filename!r}")
        try:
            payload = zf.read(info.filename)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise ValueError(f"unreadable entry {info.filename!r}: {exc}") from exc
        if info.file_size != len(payload):
            raise ValueError(f"size mismatch in {info.filename!r}")
        total += len(payload)
        if total > MAX_IMPORT_BYTES:
            raise ValueError("zip unpacks to more than 50 MB")
        if info.filename == MANIFEST_NAME:
            # The export's manifest is metadata: validated, never written back.
            try:
                parsed = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("bad manifest.json in the zip") from exc
            if not isinstance(parsed, dict) or not isinstance(parsed.get("files"), dict):
                raise ValueError("bad manifest.json in the zip")
            declared_files = parsed["files"]
            continue
        payloads[info.filename] = payload
    if not payloads:  # an export always carries at least one of the backed-up files
        raise ValueError("no REVamp data in the zip")
    # A manifest vouches for the contents: a damaged or edited backup is refused whole,
    # before anything is written. (A zip without one, put together by hand, is still accepted.)
    if declared_files is not None:
        if set(declared_files) != set(payloads):
            raise ValueError("the backup's files don't match its manifest (damaged or edited)")
        for rel, payload in payloads.items():
            if hashlib.sha256(payload).hexdigest() != declared_files[rel]:
                raise ValueError(f"{rel} is damaged (it doesn't match the backup's manifest)")

    data_dir = Path(data_dir)
    snapshot(data_dir, root=root)
    saved = _undo_point(data_dir, payloads)
    try:
        for rel, payload in sorted(payloads.items()):
            dst = data_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".import-", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(payload)
                os.chmod(tmp, 0o600)
                os.replace(tmp, dst)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
    except OSError:
        _roll_back(data_dir, saved)
        raise
    return sorted(payloads)


# ---------------------------------------------------------------------------
# Empty check + background thread
# ---------------------------------------------------------------------------

def is_empty(data_dir) -> bool:
    """True when there are no likes, progress/settings, radio favorites and no playlists
    (missing files count as empty)."""
    data_dir = Path(data_dir)
    try:
        raw = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
        likes = raw.get("likes") if isinstance(raw, dict) else None
        meaningful_state = any(raw.get(key) for key in ("resume", "resume_at", "playlist", "settings")) \
            if isinstance(raw, dict) else False
    except (OSError, ValueError):
        likes = None
        meaningful_state = False
    if likes or meaningful_state:
        return False
    try:
        settings = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = None
    if settings:
        return False
    try:
        radio = json.loads((data_dir / "radio_favorites.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        radio = None
    if radio:
        return False
    try:
        entries = os.listdir(data_dir / PLAYLIST_DIR)
    except OSError:
        return True
    for name in entries:
        if not name.lower().endswith(PLAYLIST_EXTS):
            continue
        if (data_dir / PLAYLIST_DIR / name).is_file():
            return False
    return True


# The library index (library.json) is a rebuildable cache, so snapshots skip it, but rebuilding
# it means re-reading the tags of every song (a 10k-song NAS scan pinned the CPU for minutes).
# One latest copy lives beside the snapshots; a fresh install starts from it.
INDEX_NAME = "library.json"
INDEX_COPY = "library-index.json"


def save_index_copy(data_dir, *, root=None) -> bool:
    """Keep one latest copy of the library index in the backup root. True when it was written."""
    data_dir = Path(data_dir)
    root = Path(root) if root is not None else backup_root(data_dir)
    src, dst = data_dir / INDEX_NAME, root / INDEX_COPY
    try:
        if src.is_symlink() or not src.is_file():
            return False
        if dst.is_file() and dst.stat().st_size == src.stat().st_size and dst.stat().st_mtime >= src.stat().st_mtime:
            return False
        payload = src.read_bytes()
        json.loads(payload)  # never replace a good copy with a half-written index
        root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".index-", suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, dst)
        return True
    except (OSError, ValueError):
        return False


def seed_index(data_dir, *, root=None) -> bool:
    """A data dir with no library index (a fresh install) starts from the saved copy."""
    data_dir = Path(data_dir)
    root = Path(root) if root is not None else backup_root(data_dir)
    src, dst = root / INDEX_COPY, data_dir / INDEX_NAME
    try:
        if dst.exists() or src.is_symlink() or not src.is_file():
            return False
        payload = src.read_bytes()
        json.loads(payload)
        _atomic_copy(src, dst)
        return True
    except (OSError, ValueError):
        return False


def start_thread(data_dir, *, interval=600) -> threading.Thread:
    """Daemon thread: snapshot + prune + index copy now and every *interval* seconds. Never raises."""

    def run() -> None:
        while True:
            try:
                root = backup_root(data_dir)
                snapshot(data_dir, root=root)
                prune(root)
                save_index_copy(data_dir, root=root)
            except Exception:
                pass
            time.sleep(interval)

    th = threading.Thread(target=run, name="revamp-backup", daemon=True)
    th.start()
    return th
