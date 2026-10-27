"""revamp_core.backup: private rolling backups of the user's REVamp data, outside ~/.hermes."""

from __future__ import annotations

import base64
import io
import json
import os
import stat
import sys
import time
import zipfile
from pathlib import Path

import pytest

from revamp_core import backup as bk


def make_data(d: Path, likes=("a", "b"), playlists=("Road Trip",)):
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"likes": list(likes), "volume": 70}), encoding="utf-8")
    (d / "settings.json").write_text(json.dumps({"bookRate": 1.25}), encoding="utf-8")
    (d / "radio_favorites.json").write_text(json.dumps([{"id": "s1", "name": "Groove"}]), encoding="utf-8")
    (d / "library.json").write_text('{"tracks": {}}', encoding="utf-8")
    (d / "token").write_text("secret", encoding="utf-8")
    pl = d / "playlists"; pl.mkdir(exist_ok=True)
    for name in playlists:
        (pl / f"{name.lower().replace(' ', '-')}.m3u8").write_text(f"#EXTM3U\n#EXTREVAMP-NAME:{name}\n", encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def never_real_backup_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("REVAMP_BACKUP_DIR", str(tmp_path / "backups"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    monkeypatch.setenv("REVAMP_BACKUP_DIR", str(root))
    return make_data(tmp_path / "profiles" / "dev" / "revamp"), root


@pytest.mark.skipif(sys.platform == "win32", reason="Linux/macOS home folder and permission bits")
def test_backup_root_default_is_outside_hermes_and_per_profile(tmp_path, monkeypatch):  # noqa: the env var is unset here on purpose
    monkeypatch.delenv("REVAMP_BACKUP_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    a = bk.backup_root(tmp_path / "x" / "dev" / "revamp")
    b = bk.backup_root(tmp_path / "y" / "dev" / "revamp")
    assert str(a).startswith(str(tmp_path / "home" / ".local" / "share" / "revamp" / "backups"))
    assert ".hermes" not in str(a) and a.name.startswith("dev-") and a != b
    assert stat.S_IMODE(a.stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="Linux/macOS home folder and permission bits")
def test_snapshot_contents_permissions_and_dedupe(env):
    data, root = env
    m = bk.snapshot(data, root=root)
    assert m and set(m["files"]) == {"state.json", "settings.json", "radio_favorites.json", "playlists/road-trip.m3u8"}
    snap = root / m["id"]
    assert not (snap / "library.json").exists() and not (snap / "token").exists()
    assert stat.S_IMODE(snap.stat().st_mode) == 0o700
    for f in snap.rglob("*"):
        if f.is_file():
            assert stat.S_IMODE(f.stat().st_mode) == 0o600, f
    assert bk.snapshot(data, root=root) is None  # unchanged -> no new snapshot
    (data / "state.json").write_text(json.dumps({"likes": ["a"]}), encoding="utf-8")
    m2 = bk.snapshot(data, root=root)
    assert m2 and m2["id"] != m["id"]
    assert [x["id"] for x in bk.list_backups(root)] == [m2["id"], m["id"]]
    assert not [p for p in root.iterdir() if p.name.startswith(".") or "tmp" in p.name]  # no temp leftovers


def test_snapshot_of_empty_dir_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("REVAMP_BACKUP_DIR", str(tmp_path / "b"))
    (tmp_path / "empty").mkdir()
    assert bk.snapshot(tmp_path / "empty", root=tmp_path / "b") is None


def test_prune_rules(env):
    data, root = env
    now = time.time()
    ids = []
    for i in range(8):
        (data / "state.json").write_text(json.dumps({"likes": [str(i)]}), encoding="utf-8")
        ids.append(bk.snapshot(data, root=root, now=now - (30 - i) * 86400)["id"])  # 30..23 days old
    deleted = bk.prune(root, keep_days=14, keep_min=3, keep_max=60)
    left = [x["id"] for x in bk.list_backups(root)]
    assert deleted == 5 and left == list(reversed(ids))[:3]  # all too old, but the newest 3 always stay
    for i in range(70):
        (data / "state.json").write_text(json.dumps({"likes": [f"n{i}"]}), encoding="utf-8")
        bk.snapshot(data, root=root, now=now + i)
    bk.prune(root, keep_days=14, keep_min=3, keep_max=60)
    assert len(bk.list_backups(root)) == 60


def test_restore_round_trip_and_undo(env):
    data, root = env
    m = bk.snapshot(data, root=root)
    (data / "state.json").write_text(json.dumps({"likes": []}), encoding="utf-8")
    (data / "playlists" / "road-trip.m3u8").unlink()
    (data / "playlists" / "junk.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    restored = bk.restore(m["id"], data, root=root)
    assert "state.json" in restored
    assert json.loads((data / "state.json").read_text())["likes"] == ["a", "b"]
    assert sorted(p.name for p in (data / "playlists").iterdir()) == ["road-trip.m3u8"]
    assert (data / "library.json").exists() and (data / "token").read_text() == "secret"  # untouched
    undo = bk.list_backups(root)[0]  # the pre-restore snapshot of the emptied data
    assert undo["id"] != m["id"] and "playlists/junk.m3u8" in undo["files"]
    with pytest.raises(KeyError):
        bk.restore("nope", data, root=root)


def test_zip_round_trip(env, tmp_path):
    data, root = env
    blob = bk.export_zip(data)
    names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    assert "state.json" in names and "token" not in names and "library.json" not in names
    other = make_data(tmp_path / "other" / "revamp", likes=(), playlists=())
    bk.import_zip(blob, other, root=root)
    assert json.loads((other / "state.json").read_text())["likes"] == ["a", "b"]
    assert (other / "playlists" / "road-trip.m3u8").exists()


@pytest.mark.parametrize("bad", ["../evil.json", "/etc/passwd", "playlists/../../x.m3u8", "token", "library.json",
                                 "skins/x.wsz", "playlists/a.exe"])
def test_hostile_zip_rejected_and_nothing_written(env, bad):
    data, root = env
    before = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("state.json", json.dumps({"likes": ["hacked"]}))
        z.writestr(bad, "x")
    with pytest.raises(ValueError):
        bk.import_zip(buf.getvalue(), data, root=root)
    assert {p: p.read_bytes() for p in data.rglob("*") if p.is_file()} == before


def test_zip_bomb_rejected(env):
    data, root = env
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("state.json", "0" * (60 * 1024 * 1024))
    with pytest.raises(ValueError):
        bk.import_zip(buf.getvalue(), data, root=root)


def test_is_empty(tmp_path, env):
    data, _ = env
    assert bk.is_empty(data) is False
    e = tmp_path / "e"; e.mkdir()
    assert bk.is_empty(e) is True
    make_data(e, likes=(), playlists=())
    (e / "radio_favorites.json").write_text("[]", encoding="utf-8")
    assert bk.is_empty(e) is False


def test_api_routes_end_to_end(server, tmp_path, monkeypatch):
    c = server.client
    assert c.post("/api/likes", {"track_id": c.get("/api/library/tracks?limit=1")[2]["tracks"][0]["id"]})[0] == 200
    st, _, out = c.post("/api/backups/snapshot")
    assert st == 200
    st, _, lst = c.get("/api/backups")
    assert st == 200 and lst["backups"] and lst["data_empty"] is False
    assert lst["root"] == os.environ["REVAMP_BACKUP_DIR"]
    bid = lst["backups"][0]["id"]
    assert c.post("/api/backups/restore", {"id": bid})[0] == 200
    assert c.post("/api/backups/restore", {"id": "nope"})[0] == 404
    st, hdr, blob = c.get("/api/backups/export", raw=True)
    assert st == 200 and "zip" in hdr.get("Content-Type", "") and blob[:2] == b"PK"
    assert c.post("/api/backups/import", {"zip_b64": base64.b64encode(blob).decode()})[0] == 200
    assert c.post("/api/backups/import", {"zip_b64": "bm90IGEgemlw"})[0] == 400
    assert c.get("/api/backups", headers={"X-Revamp-Token": "wrong"})[0] == 401


# ---- review round 1 (Codex) ----

def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_deleting_a_playlist_makes_a_new_snapshot(env):
    data, root = env
    assert bk.snapshot(data, root=root)
    (data / "playlists" / "road-trip.m3u8").unlink()
    m = bk.snapshot(data, root=root)  # same remaining files, but one is gone: that is a change
    assert m is not None and "playlists/road-trip.m3u8" not in m["files"]
    bk.restore(m["id"], data, root=root)
    assert not (data / "playlists" / "road-trip.m3u8").exists()  # restoring the newest does not resurrect it


def test_manifest_hashes_describe_the_saved_files_even_if_the_source_changes_mid_copy(env, monkeypatch):
    data, root = env
    real = bk.shutil.copyfile
    def racing_copy(src, dst, *a, **k):  # the player writes state.json between hashing and copying
        if str(src).endswith("state.json"):
            Path(src).write_text(json.dumps({"likes": ["written-mid-backup"]}), encoding="utf-8")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(bk.shutil, "copyfile", racing_copy)
    m = bk.snapshot(data, root=root)
    snap = root / m["id"]
    for rel, digest in m["files"].items():
        assert _sha(snap / rel) == digest, rel


@pytest.mark.parametrize("dirname", ["../evil/", "/abs/", "playlists/../../"])
def test_hostile_directory_entries_rejected(env, dirname):
    data, root = env
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("state.json", json.dumps({"likes": ["x"]}))
        z.writestr(zipfile.ZipInfo(dirname), "")
    with pytest.raises(ValueError):
        bk.import_zip(buf.getvalue(), data, root=root)


def test_symlink_entries_rejected_even_with_allowed_names(env):
    data, root = env
    before = (data / "state.json").read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("settings.json", "{}")
        link = zipfile.ZipInfo("state.json")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "/etc/passwd")
    with pytest.raises(ValueError):
        bk.import_zip(buf.getvalue(), data, root=root)
    assert (data / "state.json").read_bytes() == before


def test_oversized_declared_entry_rejected_before_reading_it(env, monkeypatch):
    data, root = env
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("state.json", "0" * (60 * 1024 * 1024))
    blob = buf.getvalue()
    def no_read(*a, **k):
        raise AssertionError("a zip bomb must be refused from its declared size, before decompressing it")
    monkeypatch.setattr(bk.zipfile.ZipFile, "read", no_read)
    monkeypatch.setattr(bk.zipfile.ZipFile, "open", no_read)
    with pytest.raises(ValueError):
        bk.import_zip(blob, data, root=root)


def test_api_import_of_a_large_but_valid_backup(server):
    c = server.client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("state.json", json.dumps({"likes": []}))
        z.writestr("playlists/big.m3u8", "#EXTM3U\n" + base64.b64encode(os.urandom(4 * 1024 * 1024)).decode())
    st, _, out = c.post("/api/backups/import", {"zip_b64": base64.b64encode(buf.getvalue()).decode()})
    assert st == 200, out  # ~7.5 MB of base64: well under the 50 MB import limit


def test_api_restore_is_seen_by_the_running_server_and_not_overwritten(server):
    c = server.client
    tid = c.get("/api/library/tracks?limit=1")[2]["tracks"][0]["id"]
    assert c.post("/api/likes", {"track_id": tid})[0] == 200
    bid = c.post("/api/backups/snapshot")[2]["backup"]["id"]
    assert c.delete(f"/api/likes?id={tid}")[0] == 200
    assert c.post("/api/backups/restore", {"id": bid})[0] == 200
    likes = lambda: json.dumps(c.get("/api/likes")[2])
    assert tid in likes(), "the server still serves the pre-restore likes from memory"
    assert c.put("/api/state", {"volume": 33})[0] == 200  # any later write must not put the old state back
    assert tid in likes()
    assert tid in (Path(server.data_dir) / "state.json").read_text()


# ---- review round 2 (Codex) ----

def test_import_does_not_write_the_manifest_into_the_data_dir(env, tmp_path):
    data, root = env
    blob = bk.export_zip(data)
    other = make_data(tmp_path / "o2" / "revamp", likes=(), playlists=())
    restored = bk.import_zip(blob, other, root=root)
    assert "manifest.json" not in restored and not (other / "manifest.json").exists()


def test_simultaneous_snapshots_never_collide(env):
    import threading
    data, root = env
    errors, results = [], []
    def one(i):
        try:
            (data / "playlists" / f"p{i}.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            results.append(bk.snapshot(data, root=root, now=1_700_000_000.0))  # same second for everyone
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    ts = [threading.Thread(target=one, args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors, errors
    ids = [m["id"] for m in results if m]
    assert len(ids) == len(set(ids))


def test_is_empty_ignores_a_folder_named_like_a_playlist(tmp_path):
    e = tmp_path / "e"; e.mkdir()
    (e / "playlists" / "not-a-playlist.m3u8").mkdir(parents=True)
    assert bk.is_empty(e) is True


def test_a_state_write_during_a_restore_cannot_undo_it(server, monkeypatch):
    import threading
    import time as _t
    c = server.client
    tid = c.get("/api/library/tracks?limit=1")[2]["tracks"][0]["id"]
    c.post("/api/likes", {"track_id": tid})
    bid = c.post("/api/backups/snapshot")[2]["backup"]["id"]
    c.delete(f"/api/likes?id={tid}")
    real = bk.restore
    side = {}
    def racing_restore(*a, **k):
        out = real(*a, **k)  # files on disk are restored now; the cache is still the old state
        side["t"] = threading.Thread(target=lambda: side.setdefault("put", c.put("/api/state", {"volume": 21})))
        side["t"].start()
        _t.sleep(0.6)  # give the player's state write every chance to land mid-restore
        return out
    monkeypatch.setattr(bk, "restore", racing_restore)
    assert c.post("/api/backups/restore", {"id": bid})[0] == 200
    side["t"].join(5)
    assert side["put"][0] == 200
    assert tid in json.dumps(c.get("/api/likes")[2]), "a concurrent state write undid the restore"
    assert tid in (Path(server.data_dir) / "state.json").read_text()


# ---- review round 3 (Codex) ----

def test_same_second_snapshots_list_newest_first_and_dedupe_against_the_newest(env):
    data, root = env
    t = 1_700_000_000.0
    a = bk.snapshot(data, root=root, now=t)
    (data / "state.json").write_text(json.dumps({"likes": ["changed"]}), encoding="utf-8")
    b = bk.snapshot(data, root=root, now=t)
    assert [m["id"] for m in bk.list_backups(root)][:2] == [b["id"], a["id"]]
    for i in range(2, 12):  # "-10" must sort after "-9"
        (data / "state.json").write_text(json.dumps({"likes": [f"v{i}"]}), encoding="utf-8")
        last = bk.snapshot(data, root=root, now=t)
    assert bk.list_backups(root)[0]["id"] == last["id"]


def test_symlinked_files_are_never_backed_up_or_exported(env):
    data, root = env
    (data / "playlists" / "leak.m3u8").symlink_to(data / "token")
    m = bk.snapshot(data, root=root)
    assert "playlists/leak.m3u8" not in m["files"]
    names = zipfile.ZipFile(io.BytesIO(bk.export_zip(data))).namelist()
    assert "playlists/leak.m3u8" not in names
    assert all(b"secret" not in zipfile.ZipFile(io.BytesIO(bk.export_zip(data))).read(n) for n in names)


def _tamper(root, snap_id, **fields):
    p = root / snap_id / "manifest.json"
    m = json.loads(p.read_text()); m.update(fields); p.write_text(json.dumps(m))


def test_a_tampered_manifest_id_cannot_make_prune_delete_outside_root(env, tmp_path):
    data, root = env
    victim = tmp_path / "victim"; victim.mkdir(); (victim / "keep.txt").write_text("x")
    ids = []
    for i in range(5):
        (data / "state.json").write_text(json.dumps({"likes": [str(i)]}), encoding="utf-8")
        ids.append(bk.snapshot(data, root=root, now=1_600_000_000.0 + i)["id"])  # all very old
    _tamper(root, ids[0], id="../victim")
    bk.prune(root, keep_days=14, keep_min=1, keep_max=60)
    assert (victim / "keep.txt").exists()
    assert {m["id"] for m in bk.list_backups(root)} <= {p.name for p in root.iterdir()}


def test_a_tampered_manifest_file_path_cannot_escape_on_restore(env, tmp_path):
    data, root = env
    m = bk.snapshot(data, root=root)
    outside = tmp_path / "profiles" / "dev" / "escaped.json"
    _tamper(root, m["id"], files={**m["files"], "../escaped.json": "0" * 64})
    with pytest.raises((ValueError, KeyError)):
        bk.restore(m["id"], data, root=root)
    assert not outside.exists()


# ---- review round 4 (Codex) ----

@pytest.mark.parametrize("damage", ["delete", "alter"])
def test_a_damaged_backup_is_refused_whole(env, damage):
    data, root = env
    m = bk.snapshot(data, root=root)
    f = root / m["id"] / "state.json"
    f.unlink() if damage == "delete" else f.write_text('{"likes": ["tampered"]}', encoding="utf-8")
    (data / "state.json").write_text(json.dumps({"likes": ["current"]}), encoding="utf-8")
    before = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        bk.restore(m["id"], data, root=root)
    assert {p: p.read_bytes() for p in data.rglob("*") if p.is_file()} == before


def test_export_manifest_matches_its_own_entries(env):
    import hashlib
    data, _ = env
    z = zipfile.ZipFile(io.BytesIO(bk.export_zip(data)))
    man = json.loads(z.read("manifest.json"))
    assert man["files"] and all(hashlib.sha256(z.read(rel)).hexdigest() == h for rel, h in man["files"].items())


def test_api_restore_of_a_damaged_backup_is_400(server):
    c = server.client
    c.post("/api/likes", {"track_id": c.get("/api/library/tracks?limit=1")[2]["tracks"][0]["id"]})
    c.post("/api/backups/snapshot")
    lst = c.get("/api/backups")[2]
    bid, root = lst["backups"][0]["id"], Path(lst["root"])
    (root / bid / "state.json").write_text("{}", encoding="utf-8")
    assert c.post("/api/backups/restore", {"id": bid})[0] == 400


def test_export_without_state_json_round_trips(env, tmp_path):
    data, root = env
    (data / "state.json").unlink()
    other = make_data(tmp_path / "o3" / "revamp", likes=(), playlists=())
    assert "settings.json" in bk.import_zip(bk.export_zip(data), other, root=root)
    with pytest.raises(ValueError):
        bk.import_zip(bk.export_zip(tmp_path / "nothing-here"), other, root=root)

def test_api_marks_empty_backups(server):
    c = server.client
    c.post("/api/backups/snapshot")  # nothing liked yet: an empty backup (or none)
    tid = c.get("/api/library/tracks?limit=1")[2]["tracks"][0]["id"]
    c.post("/api/likes", {"track_id": tid})
    c.post("/api/backups/snapshot")
    lst = c.get("/api/backups")[2]["backups"]
    assert lst[0]["empty"] is False
    assert all(isinstance(b["empty"], bool) for b in lst)
