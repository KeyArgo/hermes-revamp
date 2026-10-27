"""Winamp Skin Museum: browse/search, install (download + cache), list, remove.

The network is stubbed: museum._graphql and museum._download are replaced.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from revamp_core import museum

A = "a" * 32
B = "b" * 32
NSFW = "c" * 32


def _wsz() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("main.bmp", b"BM" + b"\x00" * 64)
    return buf.getvalue()


@pytest.fixture()
def stub(monkeypatch):
    calls = {"graphql": [], "download": []}

    def graphql(query, variables):
        calls["graphql"].append((query, variables))
        nodes = [
            {"md5": museum.BASE_MD5, "filename": "base-2.91.wsz", "nsfw": False},
            {"md5": A, "filename": "Zelda_Amp_3.wsz", "nsfw": False},
            {"md5": NSFW, "filename": "nope.wsz", "nsfw": True},
            {"md5": B, "filename": "cool_green_skin.zip", "nsfw": False},
        ]
        if "search_classic_skins" in query:
            return {"search_classic_skins": nodes[1:]}
        if "fetch_skin_by_md5" in query:
            return {"fetch_skin_by_md5": {"md5": variables["md5"], "filename": "Fetched_Name.wsz"}}
        return {"skins": {"count": 103182, "nodes": nodes}}

    def download(md5):
        calls["download"].append(md5)
        return _wsz()

    monkeypatch.setattr(museum, "_graphql", graphql)
    monkeypatch.setattr(museum, "_download", download)
    museum._CACHE.clear()
    return calls


def test_browse_filters_and_shapes(server, stub):
    code, _, body = server.client.get("/api/skins/museum?limit=4")
    assert code == 200, body
    ids = [s["md5"] for s in body["skins"]]
    assert ids == [A, B]  # base skin (that's Classic) and NSFW dropped
    first = body["skins"][0]
    assert first["id"] == f"museum:{A}"
    assert first["name"] == "Zelda Amp 3"
    assert first["screenshot"] == f"https://r2.webampskins.org/screenshots/{A}.png"
    assert first["installed"] is False
    assert body["total"] == 103182
    assert body["next"] == 4  # a full page came back, so there is more
    assert "MUSEUM" in stub["graphql"][0][0]


def test_search_uses_classic_search_without_nsfw(server, stub):
    code, _, body = server.client.get("/api/skins/museum?q=zelda&limit=10")
    assert code == 200
    query, variables = stub["graphql"][-1]
    assert "search_classic_skins" in query and variables["query"] == "zelda"
    assert variables["nsfw"] is False
    assert [s["md5"] for s in body["skins"]] == [A, B]
    assert body["next"] is None  # short page: no more


def test_browse_is_cached(server, stub):
    server.client.get("/api/skins/museum?q=x&limit=5")
    server.client.get("/api/skins/museum?q=x&limit=5")
    assert len(stub["graphql"]) == 1


def test_install_caches_and_lists(server, stub):
    code, _, entry = server.client.post("/api/skins/museum", {"md5": A, "name": "Zelda Amp 3"})
    assert code == 200, entry
    assert entry["id"] == f"museum:{A}" and entry["url"] == f"/skins/museum/{A}.wsz"
    assert (server.data_dir / "skins" / "museum" / f"{A}.wsz").exists()
    # a second install does not download again
    server.client.post("/api/skins/museum", {"md5": A, "name": "Zelda Amp 3"})
    assert stub["download"] == [A]
    # listed with the other skins, and served
    _, _, skins = server.client.get("/api/skins")
    assert [s["id"] for s in skins[:2]] == ["builtin:REVamp-Nous-Dark.wsz", "builtin:REVamp-Nous.wsz"]  # REVamp's own skins lead the list
    assert any(s["id"] == "classic" for s in skins)
    assert any(s["id"] == f"museum:{A}" and s["name"] == "Zelda Amp 3" for s in skins)
    code, _, raw = server.client.get(f"/skins/museum/{A}.wsz", raw=True)
    assert code == 200 and raw[:2] == b"PK"
    # browse now marks it installed
    _, _, body = server.client.get("/api/skins/museum?limit=4")
    assert next(s for s in body["skins"] if s["md5"] == A)["installed"] is True


def test_install_without_name_asks_the_museum(server, stub):
    code, _, entry = server.client.post("/api/skins/museum", {"md5": B})
    assert code == 200 and entry["name"] == "Fetched Name"


def test_install_rejects_bad_md5_and_non_zip(server, stub, monkeypatch):
    for bad in ("", "../../token", "A" * 32, "a" * 31):
        code, _, _ = server.client.post("/api/skins/museum", {"md5": bad})
        assert code == 400, bad
    monkeypatch.setattr(museum, "_download", lambda md5: b"<html>not a skin</html>")
    code, _, _ = server.client.post("/api/skins/museum", {"md5": B, "name": "x"})
    assert code == 502
    assert not (server.data_dir / "skins" / "museum" / f"{B}.wsz").exists()


def test_museum_down_is_502(server, monkeypatch):
    def down(query, variables):
        raise museum.MuseumError("unreachable")
    monkeypatch.setattr(museum, "_graphql", down)
    museum._CACHE.clear()
    code, _, _ = server.client.get("/api/skins/museum?q=zzz")
    assert code == 502


def test_remove(server, stub):
    server.client.post("/api/skins/museum", {"md5": A, "name": "Zelda Amp 3"})
    code, _, body = server.client.delete(f"/api/skins/museum?md5={A}")
    assert code == 200 and body["ok"] is True
    _, _, skins = server.client.get("/api/skins")
    assert not any(s["id"] == f"museum:{A}" for s in skins)
    code, _, _ = server.client.delete(f"/api/skins/museum?md5={A}")
    assert code == 404


def test_no_bundled_generated_skins(server):
    _, _, skins = server.client.get("/api/skins")
    assert not any(s["id"].startswith("builtin:revamp_") for s in skins)


@pytest.mark.skipif(not __import__("os").environ.get("REVAMP_LIVE"), reason="set REVAMP_LIVE=1 to hit skins.webamp.org")
def test_live_museum_queries():
    museum._CACHE.clear()
    popular = museum.browse(limit=5)
    assert popular["skins"] and popular["total"] > 100000
    found = museum.browse(q="zelda", limit=5)
    assert found["skins"] and all(s["md5"] != museum.BASE_MD5 for s in found["skins"])


@pytest.mark.skipif(not __import__("os").environ.get("REVAMP_LIVE"), reason="set REVAMP_LIVE=1 to hit skins.webamp.org")
def test_live_download():
    data = museum._download("1f8214678f97b888dfab7c251c943225")
    assert data[:2] == b"PK" and len(data) > 1000


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_install_rejects_bombs_and_non_skins(server, stub, monkeypatch):
    cases = {
        "no main.bmp": _zip({"readme.txt": b"hi"}),
        "zip bomb": _zip({"main.bmp": b"\x00" * (museum.MAX_UNPACKED + 1)}),
        "too many files": _zip({f"f{i}.txt": b"" for i in range(museum.MAX_ENTRIES + 1)} | {"main.bmp": b"BM"}),
        "truncated": _wsz()[:40],
    }
    for label, data in cases.items():
        monkeypatch.setattr(museum, "_download", lambda md5, d=data: d)
        code, _, _ = server.client.post("/api/skins/museum", {"md5": B, "name": "x"})
        assert code == 502, label
        assert not (server.data_dir / "skins" / "museum" / f"{B}.wsz").exists(), label
    # a skin in a subfolder with an upper-case MAIN.BMP is fine
    monkeypatch.setattr(museum, "_download", lambda md5: _zip({"Cool Skin/MAIN.BMP": b"BM"}))
    code, _, _ = server.client.post("/api/skins/museum", {"md5": B, "name": "x"})
    assert code == 200


def test_concurrent_installs_keep_every_name(server, stub):
    import threading
    md5s = [f"{i:032x}" for i in range(1, 9)]
    threads = [threading.Thread(target=museum.install, args=(m, f"Skin {m[-1]}")) for m in md5s]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    names = {e["id"][7:]: e["name"] for e in museum.installed()}
    assert all(names[m] == f"Skin {m[-1]}" for m in md5s)


def test_directory_named_main_bmp_is_not_a_skin():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("main.bmp/"), b"")
    with pytest.raises(museum.MuseumError):
        museum.check_skin(buf.getvalue())


def test_bad_cached_file_is_replaced(server, stub):
    d = server.data_dir / "skins" / "museum"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{A}.wsz").write_bytes(_zip({"readme.txt": b"old bomb"}))
    code, _, _ = server.client.post("/api/skins/museum", {"md5": A, "name": "Zelda"})
    assert code == 200 and stub["download"] == [A]
    museum.check_skin((d / f"{A}.wsz").read_bytes())


def test_index_save_failure_is_an_error(server, stub, monkeypatch):
    def boom(data):
        raise OSError("disk full")
    monkeypatch.setattr(museum, "_save_index", boom)
    code, _, _ = server.client.post("/api/skins/museum", {"md5": A, "name": "Zelda"})
    assert code == 502
    assert not (server.data_dir / "skins" / "museum" / f"{A}.wsz").exists()


def test_slow_download_does_not_block_other_skins(server, stub, monkeypatch):
    import threading
    import time as _t
    gate = threading.Event()

    def download(md5):
        if md5 == A:
            gate.wait(10)  # A's download stalls
        return _wsz()
    monkeypatch.setattr(museum, "_download", download)
    slow = threading.Thread(target=museum.install, args=(A, "Slow"))
    slow.start()
    _t.sleep(0.2)
    t0 = _t.time()
    museum.install(B, "Fast")
    assert museum.remove(B) is True
    assert _t.time() - t0 < 2
    gate.set()
    slow.join(5)
    assert any(e["name"] == "Slow" for e in museum.installed())
