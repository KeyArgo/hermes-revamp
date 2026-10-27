"""Audiobook chapters: ffprobe-read chapter lists for library tracks."""

from __future__ import annotations

import shutil
import subprocess
import time

import pytest

from revamp_core import chapters

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg needed to make test files")


def _m4b(path, titles=("Opening", "The Expedition", "Return")):
    meta = path.with_suffix(".ffmeta")
    lines = [";FFMETADATA1", "title=Test Book"]
    for i, t in enumerate(titles):
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={i * 2000}", f"END={(i + 1) * 2000}", f"title={t}"]
    meta.write_text("\n".join(lines) + "\n")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=220:d={2 * len(titles)}",
                    "-i", str(meta), "-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-f", "mp4",
                    str(path)], check=True)
    return path


def _mp3(path):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=330:d=2", str(path)], check=True)
    return path


def _wait(client, path, pred, seconds=15):
    deadline = time.time() + seconds
    body = None
    while time.time() < deadline:
        code, _, body = client.get(path)
        if code == 200 and pred(body):
            return body
        time.sleep(0.2)
    raise AssertionError(f"{path} never satisfied the condition: {body}")


@needs_ffmpeg
def test_chapters_for_m4b(tmp_path):
    chapters._CACHE.clear() if hasattr(chapters, "_CACHE") else None
    out = chapters.chapters_for(str(_m4b(tmp_path / "book.m4b")))
    assert [c["title"] for c in out] == ["Opening", "The Expedition", "Return"]
    assert [c["index"] for c in out] == [0, 1, 2]
    assert out[0]["start"] == 0.0 and abs(out[1]["start"] - 2.0) < 0.01 and abs(out[2]["end"] - 6.0) < 0.05


@needs_ffmpeg
def test_no_chapters_and_missing_files(tmp_path):
    assert chapters.chapters_for(str(_mp3(tmp_path / "song.mp3"))) == []
    assert chapters.chapters_for(str(tmp_path / "nope.m4b")) == []


@needs_ffmpeg
def test_empty_titles_get_numbers(tmp_path):
    out = chapters.chapters_for(str(_m4b(tmp_path / "untitled.m4b", titles=("", "  ", "Third"))))
    assert [c["title"] for c in out] == ["Chapter 1", "Chapter 2", "Third"]


@needs_ffmpeg
def test_ffprobe_missing_is_not_an_error(tmp_path, monkeypatch):
    path = str(_m4b(tmp_path / "b.m4b"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    if hasattr(chapters, "_CACHE"):
        chapters._CACHE.clear()
    for name in ("FFPROBE", "_FFPROBE"):
        if hasattr(chapters, name):
            monkeypatch.setattr(chapters, name, None, raising=False)
    import revamp_core.tags as tags
    for name in dir(tags):
        if "ffprobe" in name.lower() and callable(getattr(tags, name)) and name.startswith("_find"):
            monkeypatch.setattr(tags, name, lambda *a, **k: None)
    assert chapters.chapters_for(path) == []


@needs_ffmpeg
def test_cached_second_call(tmp_path, monkeypatch):
    path = str(_m4b(tmp_path / "c.m4b"))
    if hasattr(chapters, "_CACHE"):
        chapters._CACHE.clear()
    first = chapters.chapters_for(path)
    calls = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or real(*a, **k))
    assert chapters.chapters_for(path) == first and len(first) == 3
    assert not calls, "ffprobe ran again for an unchanged file"


@needs_ffmpeg
def test_route(server, tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    _m4b(books / "Route Book.m4b")
    code, _, body = server.client.put("/api/library/folders", {"folders": [
        {"path": str(server.lib), "kind": "music"}, {"path": str(books), "kind": "books"}]})
    assert code == 200, body
    server.client.post("/api/library/rescan")
    out = _wait(server.client, "/api/library/tracks?kind=audiobook", lambda b: b["total"] == 1)
    tid = out["tracks"][0]["id"]
    code, _, body = server.client.get(f"/api/library/chapters?id={tid}")
    assert code == 200 and body["id"] == tid and len(body["chapters"]) == 3
    assert server.client.get("/api/library/chapters?id=nope")[0] == 404
    assert server.client.get("/api/library/chapters")[0] == 400


def test_proxy_route_exists():
    src = open("dashboard/plugin_api.py").read()
    assert '"/library/chapters"' in src and "/api/library/chapters" in src


# ---- from the Codex review round 1 (each one a concrete failure it found)

def _fake_run(stdout, returncode=0):
    class R:
        pass
    r = R()
    r.stdout, r.stderr, r.returncode = stdout, "", returncode
    return lambda *a, **k: r


def _fresh():
    if hasattr(chapters, "_CACHE"):
        chapters._CACHE.clear()


def test_failing_ffprobe_with_partial_json_gives_nothing(tmp_path, monkeypatch):
    f = tmp_path / "x.m4b"; f.write_bytes(b"\0" * 64); _fresh()
    out = '{"chapters":[{"start_time":"0","end_time":"1","tags":{"title":"A"}}]}'
    monkeypatch.setattr(subprocess, "run", _fake_run(out, returncode=1))
    assert chapters.chapters_for(str(f)) == []


def test_bad_paths_never_raise():
    assert chapters.chapters_for("\0") == []
    assert chapters.chapters_for("") == []


def test_odd_json_shapes_never_raise(tmp_path, monkeypatch):
    f = tmp_path / "y.m4b"; f.write_bytes(b"\0" * 64)
    for out in ("[]", '{"chapters": 5}', '{"chapters": [1, "x", null]}', '{"chapters": [{"tags": "nope"}]}', "not json"):
        _fresh()
        monkeypatch.setattr(subprocess, "run", _fake_run(out))
        assert chapters.chapters_for(str(f)) == [], out


def test_unordered_chapters_are_numbered_after_sorting(tmp_path, monkeypatch):
    f = tmp_path / "z.m4b"; f.write_bytes(b"\0" * 64); _fresh()
    out = '{"chapters":[{"start_time":"10","end_time":"20","tags":{}},{"start_time":"0","end_time":"10","tags":{}}]}'
    monkeypatch.setattr(subprocess, "run", _fake_run(out))
    got = chapters.chapters_for(str(f))
    assert [(c["index"], c["title"], c["start"]) for c in got] == [(0, "Chapter 1", 0.0), (1, "Chapter 2", 10.0)]


def test_concurrent_first_calls_run_ffprobe_once(tmp_path, monkeypatch):
    import threading
    f = tmp_path / "w.m4b"; f.write_bytes(b"\0" * 64); _fresh()
    calls = []
    out = '{"chapters":[{"start_time":"0","end_time":"5","tags":{"title":"Only"}}]}'
    slow = _fake_run(out)

    def run(*a, **k):
        calls.append(1)
        time.sleep(0.3)
        return slow()
    monkeypatch.setattr(subprocess, "run", run)
    results = []
    threads = [threading.Thread(target=lambda: results.append(chapters.chapters_for(str(f)))) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(calls) == 1, f"ffprobe ran {len(calls)} times for one file"
    assert all(r and r[0]["title"] == "Only" for r in results)


def test_ffprobe_reads_the_resolved_file(tmp_path, monkeypatch):
    real = tmp_path / "real.m4b"; real.write_bytes(b"\0" * 64)
    link = tmp_path / "link.m4b"; link.symlink_to(real); _fresh()
    seen = []
    def run(args, *a, **k):
        seen.append(list(args))
        return _fake_run('{"chapters":[]}')()
    monkeypatch.setattr(subprocess, "run", run)
    chapters.chapters_for(str(link))
    assert seen and str(real) in seen[0] and str(link) not in seen[0], "probe the file the cache key names"
