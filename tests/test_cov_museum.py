"""Coverage for revamp_core/museum.py: graphql/download primitives (faked
urlopen), cache bounds, zip validation, install/remove failure paths."""

from __future__ import annotations

import io
import json
import pathlib
import threading
import zipfile

import pytest
import sys

from revamp_core import museum
from revamp_core.museum import MuseumError

MD5 = "abcdef0123456789abcdef0123456789"


def skin_zip_bytes(extra=None, main=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if main:
            zf.writestr("skin/main.bmp", b"BMP")
        for name, blob in (extra or {}).items():
            zf.writestr(name, blob)
    return buf.getvalue()


@pytest.fixture()
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(museum, "data_dir", lambda: tmp_path)
    saved = dict(museum._CACHE)
    museum._CACHE.clear()
    yield tmp_path
    museum._CACHE.clear()
    museum._CACHE.update(saved)


class Url:
    """Routes faked urlopen calls for the graphql API and the skin CDN."""

    def __init__(self, graphql=None, skins=None, fail=False):
        self.graphql, self.skins, self.fail = graphql, skins or {}, fail
        self.requests = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.requests.append((url, req))
        if self.fail:
            raise OSError("network down")
        if "graphql" in url:
            assert req.get_header("Content-type") == "application/json"
            payload = json.loads(req.data)
            if isinstance(self.graphql, Exception):
                raise self.graphql
            body = json.dumps({"data": self.graphql(payload)}
                              if callable(self.graphql) else self.graphql)
        else:
            md5 = url.rsplit("/", 1)[-1].split(".")[0]
            data = self.skins.get(md5, self.skins.get("*"))
            if data is None:
                raise OSError(f"404 for {md5}")
            return _Resp(data)
        return _Resp(body.encode("utf-8"))


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self, n=-1):
        return self.body[:n] if n and n > 0 else self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen(monkeypatch, **kw):
    obj = Url(**kw)
    monkeypatch.setattr(museum.urllib.request, "urlopen", obj)
    obj.timeout_seen = None
    return obj


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

def test_graphql_success_and_errors(monkeypatch):
    u = _urlopen(monkeypatch, graphql=lambda p: {"skins": {"count": 1}})
    assert museum._graphql("q", {"a": 1}) == {"skins": {"count": 1}}
    url, req = u.requests[0]
    assert url == museum.GRAPHQL and req.get_method() == "POST"
    assert json.loads(req.data)["variables"] == {"a": 1}

    _urlopen(monkeypatch, fail=True)
    with pytest.raises(MuseumError, match="graphql failed"):
        museum._graphql("q", {})
    _urlopen(monkeypatch, graphql={"errors": [{"m": "x"}]})
    with pytest.raises(MuseumError, match="graphql errors"):
        museum._graphql("q", {})
    _urlopen(monkeypatch, graphql=[1, 2])            # not a dict
    with pytest.raises(MuseumError, match="graphql errors"):
        museum._graphql("q", {})
    _urlopen(monkeypatch, graphql={"data": "nope"})  # data not a dict
    with pytest.raises(MuseumError, match="no data"):
        museum._graphql("q", {})


def test_download_paths(monkeypatch):
    u = _urlopen(monkeypatch, skins={MD5: b"ZIPDATA"})
    assert museum._download(MD5) == b"ZIPDATA"
    assert u.requests[0][1].get_header("User-agent") == museum.UA
    _urlopen(monkeypatch, skins={"*": b"x" * (museum.MAX_BYTES + 1)})
    with pytest.raises(MuseumError, match="10 MiB"):
        museum._download(MD5)
    _urlopen(monkeypatch, fail=True)
    with pytest.raises(MuseumError, match="download failed"):
        museum._download(MD5)


def test_cache_expiry_and_bounded_eviction(monkeypatch):
    calls = []

    def fn():
        calls.append(1)
        return "v"
    assert museum._cached(("k",), fn) == "v"
    assert museum._cached(("k",), fn) == "v" and len(calls) == 1   # fresh hit
    stamp, _ = museum._CACHE[("k",)]
    museum._CACHE[("k",)] = (stamp - museum.TTL - 1, "stale")
    assert museum._cached(("k",), fn) == "v" and len(calls) == 2   # expired
    for i in range(256):
        museum._CACHE[("junk", i)] = (0, i)
    museum._cached(("fresh",), fn)                                 # clears then stores
    assert len(museum._CACHE) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
def test_dir_mkdir_oserror(monkeypatch, home):
    real = pathlib.Path.mkdir

    def boom(self, *a, **k):
        raise OSError("ro")
    monkeypatch.setattr(pathlib.Path, "mkdir", boom)
    assert str(museum._dir()).endswith("skins/museum")
    monkeypatch.setattr(pathlib.Path, "mkdir", real)


# ---------------------------------------------------------------------------
# validation + index plumbing
# ---------------------------------------------------------------------------

def test_check_skin_rejects():
    with pytest.raises(MuseumError, match="not a skin archive"):
        museum.check_skin(b"not a zip at all")
    too_many = {"f%d.bmp" % i: b"x" for i in range(museum.MAX_ENTRIES + 1)}
    with pytest.raises(MuseumError, match="too many files"):
        museum.check_skin(skin_zip_bytes(too_many))
    with pytest.raises(MuseumError, match="unpacks too large"):
        museum.check_skin(skin_zip_bytes({"big.bmp": b"z" * (museum.MAX_UNPACKED + 1)}))
    with pytest.raises(MuseumError, match="no main.bmp"):
        museum.check_skin(skin_zip_bytes({"other.bmp": b"x"}, main=False))
    museum.check_skin(skin_zip_bytes())                            # accepts


def test_valid_pretty_entry_index(home):
    assert museum.valid(MD5) and not museum.valid(MD5.upper()[:-1])
    assert museum.pretty("My_Cool__Skin.WSZ") == "My Cool Skin"
    assert museum.entry(MD5)["name"] == MD5                        # no index -> md5
    (home / "skins" / "museum").mkdir(parents=True, exist_ok=True)
    (home / "skins" / "museum" / "index.json").write_text(
        json.dumps({MD5: {"name": "Saved"}, "bad": "notadict"}))
    assert museum.entry(MD5)["name"] == "Saved"
    assert museum.entry("0" * 32)["name"] == "0" * 32              # unknown
    assert museum._stored_name(MD5) == "Saved"
    assert museum._stored_name("0" * 32) == ""


def test_save_index_oserror_cleans_tmp(home, monkeypatch):
    import os
    (home / "skins" / "museum").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        museum._save_index({MD5: {"name": "n"}})
    assert not list((home / "skins" / "museum").glob(".index-*.tmp"))


def test_load_index_corrupt(home):
    (home / "skins" / "museum").mkdir(parents=True, exist_ok=True)
    (home / "skins" / "museum" / "index.json").write_text("{ broken")
    assert museum._load_index() == {}
    (home / "skins" / "museum" / "index.json").write_text("[1]")
    assert museum._load_index() == {}
    assert museum._load_index() == {}                              # missing file too


# ---------------------------------------------------------------------------
# install / remove
# ---------------------------------------------------------------------------

def test_install_happy_cached_and_names(home, monkeypatch):
    _urlopen(monkeypatch, skins={"*": skin_zip_bytes()},
             graphql=lambda p: {"fetch_skin_by_md5": {"filename": "Neat_Skin.wsz"}})
    e = museum.install(MD5, name="My Name")
    assert e["name"] == "My Name" and e["url"] == f"/skins/museum/{MD5}.wsz"
    assert (home / "skins" / "museum" / f"{MD5}.wsz").is_file()
    assert json.loads((home / "skins" / "museum" / "index.json").read_text()) \
        == {MD5: {"name": "My Name"}}

    u = _urlopen(monkeypatch, skins={})                             # no network now
    assert museum.install(MD5)["name"] == "My Name"                 # cached path
    assert u.requests == []                                         # no download

    _urlopen(monkeypatch, skins={"*": skin_zip_bytes()},
             graphql=lambda p: {"fetch_skin_by_md5": {"filename": "GraphQL_X.wsz"}})
    assert museum.install("1" * 32)["name"] == "GraphQL X"          # graphql name


def test_install_corrupt_then_refetch_and_failures(home, monkeypatch):
    d = home / "skins" / "museum"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{MD5}.wsz").write_bytes(b"garbage cached")               # old/bad cache
    _urlopen(monkeypatch, skins={"*": skin_zip_bytes()}, graphql={})
    assert museum.install(MD5)["name"] == MD5                       # refetched

    _urlopen(monkeypatch, skins={"*": b"NOTZIP"})
    with pytest.raises(MuseumError, match="not a wsz/zip"):
        museum.install("2" * 32)

    _urlopen(monkeypatch, skins={"*": b"PK" + b"\x00" * 50})        # PK but not a zip
    with pytest.raises(MuseumError):
        museum.install("3" * 32)

    monkeypatch.setattr(museum, "_graphql",
                        lambda *a, **k: (_ for _ in ()).throw(MuseumError("api down")))
    _urlopen(monkeypatch, skins={"*": skin_zip_bytes()})
    assert museum.install("4" * 32)["name"] == "4" * 32             # graphql fails -> md5

    def boom(path):
        raise OSError("full")
    monkeypatch.setattr(museum, "_save_index", boom)
    with pytest.raises(MuseumError, match="could not save the skin list"):
        museum.install("5" * 32)
    assert not (d / ("5" * 32 + ".wsz")).exists()                   # half-install rolled back

    def wboom(md5, data):
        raise OSError("io")
    monkeypatch.setattr(museum, "_save_index", museum._save_index)
    monkeypatch.setattr(museum, "_write_skin", wboom)
    with pytest.raises(MuseumError, match="could not save the skin"):
        museum.install("6" * 32)
    with pytest.raises(ValueError):
        museum.install("not-an-md5")


def test_write_skin_oserror_cleans_tmp(home, monkeypatch):
    import os
    (home / "skins" / "museum").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        museum._write_skin(MD5, b"data")
    assert not list((home / "skins" / "museum").glob(".skin-*.tmp"))


def test_remove_paths(home, monkeypatch):
    assert museum.remove("bad-md5") is False
    assert museum.remove(MD5) is False                              # not installed
    d = home / "skins" / "museum"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{MD5}.wsz"
    p.write_bytes(b"x")
    museum._save_index({MD5: {"name": "n"}, "other": {"name": "o"}})

    real_unlink = pathlib.Path.unlink

    def boom(self, *a, **k):
        if self.name.endswith(".wsz"):
            raise OSError("busy")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "unlink", boom)
    assert museum.remove(MD5) is False
    monkeypatch.setattr(pathlib.Path, "unlink", real_unlink)

    def save_boom(index):
        raise OSError("disk")
    monkeypatch.setattr(museum, "_save_index", save_boom)
    assert museum.remove(MD5) is True                               # index save fails
    assert not p.exists() and museum.installed() == []
    monkeypatch.setattr(museum, "_save_index", museum._save_index)

    p.write_bytes(b"y")
    assert museum.remove(MD5) is True
