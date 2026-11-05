"""A book in several files remembers which file you were in (user report 11-03: a Blackstone
edition in 001.mp3 ... 009.mp3 started over at 001 every time)."""
import os

import pytest

from revamp_core import items, library, server, store


@pytest.fixture
def book(monkeypatch, tmp_path):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    folder = "/books/Mountains"
    tracks = {f"p{n}": {"id": f"p{n}", "path": f"{folder}/Mountains - {n:03d}.mp3", "album": "Mountains",
                        "genre": "Audiobook", "duration": 1500} for n in range(1, 10)}
    tracks["other"] = {"id": "other", "path": "/books/Other/Other - 001.mp3", "album": "Other", "genre": "Audiobook"}
    tracks["song"] = {"id": "song", "path": "/music/a.mp3", "album": "Mountains", "genre": "Rock"}
    monkeypatch.setattr(library, "_INDEX", tracks)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    clock = {"t": 1000.0}
    monkeypatch.setattr(store.time, "time", lambda: clock["t"])

    def save(tid, pos, at):
        clock["t"] = at
        store.set_resume(items.local_id(tid), pos)
    return save


def test_starting_the_book_goes_on_in_the_file_you_were_last_in(book):
    book("p9", 818, 2000)   # 009 at 13:38, the real place
    book("p1", 1, 3000)     # 001 at 0:01, newer but only "just started"
    assert server._book_continue("p1") == {"id": "p9", "position": 818, "part": 9, "parts": 9}


def test_the_newest_real_place_wins(book):
    book("p9", 818, 2000)
    book("p2", 600, 2500)
    assert server._book_continue("p1")["id"] == "p2"


def test_a_file_picked_on_purpose_plays_as_asked(book):
    book("p9", 818, 2000)
    assert server._book_continue("p5") == {}, "only starting from the first file jumps"


def test_nothing_to_go_on_from(book):
    assert server._book_continue("p1") == {}
    book("p1", 700, 2000)  # the first file itself holds the place: its own resume handles it
    assert server._book_continue("p1") == {}


def test_single_files_music_and_unknown_ids_never_jump(book):
    assert server._book_continue("other") == {}
    assert server._book_continue("song") == {}
    assert server._book_continue("nope") == {}
    assert server._book_continue("") == {}


def test_part_one_reached_from_inside_the_book_plays_as_asked(book):
    # Claude a10r11: Previous, repeat-all or a pick of part 1 while in the book bounced back.
    book("p9", 818, 2000)
    assert server._book_continue("p1", "p2") == {}, "Previous from part 2"
    assert server._book_continue("p1", "p9") == {}, "repeat-all, or part 1 picked after the jump"
    assert server._book_continue("p1", "song")["id"] == "p9", "started from music: go on"
    assert server._book_continue("p1", "")["id"] == "p9", "nothing before: go on"


def test_stopping_at_the_end_of_a_part_goes_on_with_the_next(book):
    # Grok a10r11: the last 30 s of a part erased its place, so continue went back to an older
    # part. The place is kept; continue goes on with the next part from its start.
    book("p3", 480, 2000)
    book("p8", 1490, 3000)  # 8 of 25 minutes left? no: 10 s before the end of part 8
    assert server._book_continue("p1") == {"id": "p9", "position": 0, "part": 9, "parts": 9}


def test_a_finished_book_starts_over_at_part_one(book):
    book("p1", 700, 1000)   # an old place in part 1 must not be where it starts over
    book("p9", 1495, 3000)
    assert server._book_continue("p1") == {"restart": True}


def test_a_finished_part_shows_no_left_off_time(book):
    book("p8", 1490, 3000)
    book("p3", 480, 2000)
    found = library.search(q="", kind="audiobook")
    rows = {r["id"]: r for r in (found.get("tracks") if isinstance(found, dict) else found)}
    assert "resume" not in rows["p8"] and rows["p3"]["resume"] == 480


def test_the_endpoint_answers_through_the_api(server_live):
    client, ids = server_live
    code, _, body = client.get(f"/api/resume/book?id={ids[0]}")
    assert code == 200 and body == {"id": ids[2], "position": 300, "part": 3, "parts": 3}
    code, _, body = client.get(f"/api/resume/book?id={ids[0]}&from={ids[1]}")
    assert code == 200 and body == {}
    code, _, _ = client.get(f"/api/resume/book?id={ids[0]}", headers={"X-Revamp-Token": "wrong"})
    assert code == 401


@pytest.fixture
def server_live(server, monkeypatch):
    monkeypatch.setattr(library, "_kinds_map", lambda: {})  # no folder kinds: the genre decides
    from conftest import Client
    from pathlib import Path
    folder = Path(server.lib) / "Books" / "Saga"
    folder.mkdir(parents=True)
    for n in (1, 2, 3):
        (folder / f"Saga - {n:03d}.mp3").write_bytes(b"\x00" * 64)
    library.scan_sync([str(server.lib)])
    parts = sorted((t for t in library.all_tracks() if "/Books/Saga/" in t["path"].replace("\\", "/")), key=lambda t: t["path"])
    ids = [t["id"] for t in parts]
    client = Client(server.base, server.token)
    for t in parts:
        library._INDEX[t["id"]] = dict(t, genre="Audiobook", album="Saga")
    assert client.post("/api/resume", {"key": items.local_id(ids[2]), "position": 300})[0] == 200
    return client, ids


def test_parts_follow_the_queue_order_not_the_file_names(monkeypatch, tmp_path):
    # Claude a10r12: "Chapter 10" sorted before "Chapter 2" by path; next part skipped 8 chapters.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    tracks = {f"c{n}": {"id": f"c{n}", "path": f"/b/Saga/Chapter {n}.mp3", "album": "Saga",
                        "genre": "Audiobook", "duration": 600} for n in range(1, 13)}
    monkeypatch.setattr(library, "_INDEX", tracks)
    clock = {"t": 1000.0}
    monkeypatch.setattr(store.time, "time", lambda: clock["t"])
    clock["t"] = 2000
    store.set_resume(items.local_id("c1"), 590)  # stopped at the end of Chapter 1
    assert server._book_continue("c1") == {"id": "c2", "position": 0, "part": 2, "parts": 12}
    clock["t"] = 3000
    store.set_resume(items.local_id("c9"), 595)  # the end of Chapter 9 is not the end of the book
    assert server._book_continue("c1")["id"] == "c10"


def test_track_numbers_win_over_names(monkeypatch, tmp_path):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    tracks = {"intro": {"id": "intro", "path": "/b/Story/Intro.mp3", "album": "S", "genre": "Audiobook", "track_no": 1},
              "ch1": {"id": "ch1", "path": "/b/Story/Chapter 01.mp3", "album": "S", "genre": "Audiobook", "track_no": 2}}
    monkeypatch.setattr(library, "_INDEX", tracks)
    store.set_resume(items.local_id("ch1"), 300)
    assert server._book_continue("intro")["id"] == "ch1", "Intro (track 1) is the first part"
    assert server._book_continue("ch1") == {}


def test_untagged_books_in_one_folder_are_separate_books(monkeypatch, tmp_path):
    # Claude a10r12: two untagged books in /Audiobooks were one "book".
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    tracks = {"a": {"id": "a", "path": "/Audiobooks/A.m4b", "genre": "Audiobook"},
              "b": {"id": "b", "path": "/Audiobooks/B.m4b", "genre": "Audiobook"}}
    monkeypatch.setattr(library, "_INDEX", tracks)
    store.set_resume(items.local_id("b"), 900)
    assert server._book_continue("a") == {}


def test_replaying_a_finished_part_keeps_its_finished_mark(book):
    # Grok a10r12: the first seconds of a replay overwrote the finished mark, so continue later
    # went back to an older chapter.
    book("p3", 480, 2000)
    book("p8", 1490, 3000)  # finished part 8
    assert server._keeps_finished_mark(items.local_id("p8"), 2) is True
    assert server._keeps_finished_mark(items.local_id("p8"), 31) is False, "a real new place wins"
    assert server._keeps_finished_mark(items.local_id("p3"), 2) is False, "only a finished part"
    assert server._book_continue("p1")["id"] == "p9"


def test_numbered_untagged_parts_in_one_folder_are_one_book(monkeypatch, tmp_path):
    # Grok a10r12: 001.mp3 ... with no album tag never continued.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    tracks = {f"u{n}": {"id": f"u{n}", "path": f"/b/Untagged/{n:03d}.mp3", "genre": "Audiobook"} for n in (1, 2, 3)}
    monkeypatch.setattr(library, "_INDEX", tracks)
    store.set_resume(items.local_id("u3"), 400)
    assert server._book_continue("u1")["id"] == "u3"


def test_a_non_ascii_digit_in_a_file_name_does_not_break_continue():
    # Grok a10r12: int("１２") raised, so the whole book never continued.
    assert library.natural("第１２章.mp3") and library.natural("Chapter 2") < library.natural("Chapter 10")


def test_the_api_keeps_a_finished_mark(server_live):
    client, ids = server_live
    library._INDEX[ids[1]] = dict(library._INDEX[ids[1]], duration=600)
    assert client.post("/api/resume", {"key": items.local_id(ids[1]), "position": 590})[0] == 200
    code, _, body = client.post("/api/resume", {"key": items.local_id(ids[1]), "position": 3})
    assert code == 200 and body.get("kept") == "finished"
    assert client.get("/api/resume?key=" + items.local_id(ids[1]))[2]["position"] == 590


@pytest.mark.parametrize("layout", ["untagged chapters", "two discs"])
def test_the_books_queue_and_continue_use_one_order(monkeypatch, tmp_path, layout):
    # Codex a10r13: the Books view sorted by title without disc or numbers-as-numbers, so the queue
    # ran 1, 10, 2 while continue said 2. Both now use library.album_order.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    if layout == "untagged chapters":
        spec = [("c1", "Chapter 1", None, None), ("c2", "Chapter 2", None, None), ("c10", "Chapter 10", None, None)]
    else:
        spec = [("d1t1", "One", 1, 1), ("d1t2", "Two", 1, 2), ("d2t1", "Three", 2, 1), ("d2t2", "Four", 2, 2)]
    tracks = {}
    for tid, title, disc, trk in spec:
        tracks[tid] = {"id": tid, "path": f"/b/Book/{title}.mp3", "title": title, "album": "The Book",
                       "genre": "Audiobook", "duration": 600, "disc_no": disc, "track_no": trk}
    monkeypatch.setattr(library, "_INDEX", tracks)
    found = library.search(q="", kind="audiobook")
    queue = [r["id"] for r in (found.get("tracks") if isinstance(found, dict) else found)]
    assert queue == [s[0] for s in spec], "the Books list (the queue) plays the book in order"
    store.set_resume(items.local_id(queue[0]), 590)  # finished the first part
    assert server._book_continue(queue[0])["id"] == queue[1], "continue goes on with what the queue plays next"


def test_a_place_past_a_too_short_stored_length_is_not_finished(book, monkeypatch):
    # Grok a10r13: a VBR guess said 8 min; the person stopped 15 min into a 30 min part.
    library._INDEX["p8"] = dict(library._INDEX["p8"], duration=480)
    book("p3", 300, 2000)
    book("p8", 900, 3000)
    assert server._book_continue("p1") == {"id": "p8", "position": 900, "part": 8, "parts": 9}
    assert server._keeps_finished_mark(items.local_id("p8"), 2) is False
    assert library.near_end(470, 480) and not library.near_end(900, 480) and not library.near_end(400, 0)


def test_album_case_does_not_split_a_book(monkeypatch, tmp_path):
    # Grok a10r13: "Dune" and "dune" were one album in Books but two books here.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    tracks = {"d1": {"id": "d1", "path": "/b/Dune/01.mp3", "title": "01", "album": "Dune", "genre": "Audiobook"},
              "d2": {"id": "d2", "path": "/b/Dune/02.mp3", "title": "02", "album": "dune ", "genre": "Audiobook"}}
    monkeypatch.setattr(library, "_INDEX", tracks)
    store.set_resume(items.local_id("d2"), 400)
    assert server._book_continue("d1")["id"] == "d2"


def test_the_real_scanner_keeps_separate_books_in_one_folder_apart(monkeypatch, tmp_path):
    # Grok a10r15: the scanner fills a missing album with the folder's name, so Dune.m4b and
    # Foundation.m4b in Audiobooks/ were "parts 1 and 2" of one book.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_INDEX", {})
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    shelf = tmp_path / "Audiobooks"
    shelf.mkdir()
    for name in ("Dune", "Foundation", "1984", "Fahrenheit 451"):
        (shelf / f"{name}.m4b").write_bytes(b"\x00" * 256)
    saga = tmp_path / "Saga"
    saga.mkdir()
    for n in (1, 2, 3):
        (saga / f"Saga {n:02d}.m4b").write_bytes(b"\x00" * 256)
    library.scan_sync([str(shelf), str(saga)])
    by_name = {os.path.basename(t["path"]): t for t in library.all_tracks()}
    assert by_name["Dune.m4b"].get("album") == "Audiobooks", "the scanner's folder-name fallback"
    assert by_name["Dune.m4b"].get("album_from_folder") is True, "and it says so (grok a10r19)"
    store.set_resume(items.local_id(by_name["Foundation.m4b"]["id"]), 3600)
    store.set_resume(items.local_id(by_name["Fahrenheit 451.m4b"]["id"]), 3600)
    for first in ("1984.m4b", "Dune.m4b"):
        assert server._book_continue(by_name[first]["id"]) == {}, first
    store.set_resume(items.local_id(by_name["Saga 03.m4b"]["id"]), 120)
    assert server._book_continue(by_name["Saga 01.m4b"]["id"])["id"] == by_name["Saga 03.m4b"]["id"]


def test_parts_follow_the_books_list_even_with_differing_artists(monkeypatch, tmp_path):
    # Codex a10r15: chapter 1 by Zoe, chapter 2 by Amy: the Books list (artist first) plays
    # chapter 2 first, so continue must use that same order.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    tracks = {"z1": {"id": "z1", "path": "/b/Tale/1.mp3", "title": "1", "album": "A Tale", "artist": "Zoe", "genre": "Audiobook", "track_no": 1},
              "a2": {"id": "a2", "path": "/b/Tale/2.mp3", "title": "2", "album": "A Tale", "artist": "Amy", "genre": "Audiobook", "track_no": 2}}
    monkeypatch.setattr(library, "_INDEX", tracks)
    found = library.search(q="", kind="audiobook")
    queue = [r["id"] for r in (found.get("tracks") if isinstance(found, dict) else found)]
    store.set_resume(items.local_id(queue[1]), 300)
    assert server._book_continue(queue[0])["id"] == queue[1], "continue follows the list's order"


# ---- Round 16 (hunt a10r16) ------------------------------------------------------------

def _short_book(monkeypatch, tmp_path, durations):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    tracks = {f"p{n}": {"id": f"p{n}", "path": f"/b/Saga/Saga - {n:03d}.mp3", "album": "Saga",
                        "genre": "Audiobook", "duration": dur} for n, dur in durations.items()}
    monkeypatch.setattr(library, "_INDEX", tracks)
    clock = {"t": 1000.0}
    monkeypatch.setattr(store.time, "time", lambda: clock["t"])

    def save(tid, pos, at):
        clock["t"] = at
        store.set_resume(items.local_id(tid), pos)
    return save


def test_a_short_part_takes_you_on_to_the_part_after_it(monkeypatch, tmp_path):
    # Codex a10r16: a 20 s part never reached the 30 s minimum, so its place was invisible and
    # continue replays the part before it.
    save = _short_book(monkeypatch, tmp_path, {1: 1500, 2: 20, 3: 1500})
    save("p1", 1490, 1000)  # finished part 1
    save("p2", 18, 2000)    # finished the 20 s part 2
    save("p3", 10, 3000)    # 10 s into part 3: "just started", not a place
    assert server._book_continue("p1") == {"id": "p3", "position": 0, "part": 3, "parts": 3}


def test_a_short_last_part_can_finish_the_book(monkeypatch, tmp_path):
    save = _short_book(monkeypatch, tmp_path, {1: 1500, 2: 20})
    save("p1", 1490, 1000)
    save("p2", 19, 2000)   # the end of a 20 s last part is the end of the book
    assert server._book_continue("p1") == {"restart": True}
    assert server._keeps_finished_mark(items.local_id("p2"), 2) is True


def test_a_place_in_the_middle_of_a_short_part_is_not_finished(monkeypatch, tmp_path):
    save = _short_book(monkeypatch, tmp_path, {1: 1500, 2: 20})
    save("p2", 9, 2000)    # halfway through a 20 s part: not finished
    assert server._book_continue("p1") == {}
    assert server._keeps_finished_mark(items.local_id("p2"), 2) is False


def test_a_zero_place_from_the_page_keeps_the_finished_mark(server_live):
    # Grok a10r16: the page floors 0.4 s to 0 and 0 meant "clear", so replaying a finished part
    # erased the finished mark.
    client, ids = server_live
    library._INDEX[ids[1]] = dict(library._INDEX[ids[1]], duration=600)
    assert client.post("/api/resume", {"key": items.local_id(ids[1]), "position": 590})[0] == 200
    code, _, body = client.post("/api/resume", {"key": items.local_id(ids[1]), "position": 0})
    assert code == 200 and body.get("kept") == "finished"
    assert client.get("/api/resume?key=" + items.local_id(ids[1]))[2]["position"] == 590
    # An explicit null still clears it.
    assert client.post("/api/resume", {"key": items.local_id(ids[1]), "position": None})[0] == 200
    assert client.get("/api/resume?key=" + items.local_id(ids[1]))[2]["position"] in (None, 0)



def test_a_place_the_page_calls_the_end_is_a_finished_mark(monkeypatch, tmp_path):
    # Grok a10r17 (high): _finished_place shrank the margin to 10% of the file, but the page still
    # refuses to seek into the last 30 s. A place in that gap was "real" on the server and "the
    # end" on the page, so replaying the part overwrote it with 0:01 and the place was lost.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "_INDEX", {"c1": {"id": "c1", "path": "/b/Book/01.mp3", "album": "Book",
                                                   "genre": "Audiobook", "duration": 120}})
    store.set_resume(items.local_id("c1"), 100)   # 1:40 of a 2-minute chapter
    assert server._keeps_finished_mark(items.local_id("c1"), 1) is True
    assert server._keeps_finished_mark(items.local_id("c1"), 0) is True
    assert server._keeps_finished_mark(items.local_id("c1"), 31) is False, "a real new place wins"
    assert server._finished_place(library._INDEX["c1"], 95) is True
    assert server._finished_place(library._INDEX["c1"], 60) is False
    # A file shorter than 30 s keeps the small window from round 16.
    assert server._finished_place({"duration": 20}, 19) is True
    assert server._finished_place({"duration": 20}, 9) is False


def test_numbered_titles_in_a_same_named_folder_are_one_book(monkeypatch, tmp_path):
    # Grok a10r17: album tag == folder name ("The Hobbit" in The Hobbit/), parts named
    # "01 - An Unexpected Party" / "02 - Roast Mutton": the identical-stem rule rejected them, so
    # continue never ran. Distinct track numbers prove the order by themselves.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "_INDEX", {
        "t1": {"id": "t1", "path": "/b/The Hobbit/01 - An Unexpected Party.mp3", "album": "The Hobbit",
               "genre": "Audiobook", "duration": 1800, "track_no": 1, "album_from_folder": False},
        "t2": {"id": "t2", "path": "/b/The Hobbit/02 - Roast Mutton.mp3", "album": "The Hobbit",
               "genre": "Audiobook", "duration": 1800, "track_no": 2, "album_from_folder": False}})
    store.set_resume(items.local_id("t2"), 300)
    assert server._book_continue("t1")["id"] == "t2"


def test_two_numbered_books_in_one_folder_stay_apart(monkeypatch, tmp_path):
    # The guard on the rule above: two books each numbered from 1 in the same folder are not one
    # book, so duplicate track numbers fall back to the strict file-name check.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "_INDEX", {
        "a1": {"id": "a1", "path": "/Audiobooks/01 - Dune.mp3", "album": "Audiobooks", "genre": "Audiobook", "track_no": 1},
        "a2": {"id": "a2", "path": "/Audiobooks/01 - Foundation.mp3", "album": "Audiobooks", "genre": "Audiobook", "track_no": 1},
        "a3": {"id": "a3", "path": "/Audiobooks/02 - Foundation.mp3", "album": "Audiobooks", "genre": "Audiobook", "track_no": 2}})
    store.set_resume(items.local_id("a3"), 300)
    assert server._book_continue("a1") == {}


def test_finishing_a_short_part_again_is_the_newest_mark(monkeypatch, tmp_path):
    # Codex a10r18: _keeps_finished_mark kept the OLD mark for every report up to 30 s, so a short
    # part listened through and finished a second time never got a new timestamp; continue then
    # went back to an older part instead of going on.
    save = _short_book(monkeypatch, tmp_path, {1: 1500, 2: 20, 3: 1500})
    save("p1", 1490, 1000)          # finished part 1
    save("p2", 19, 2000)            # finished the 20 s part 2
    # Replaying part 2: the opening seconds keep the finished mark ...
    assert server._keeps_finished_mark(items.local_id("p2"), 3) is True
    # ... but finishing it again is a real new place, stored with a new time.
    assert server._keeps_finished_mark(items.local_id("p2"), 19) is False
    save("p2", 19, 3000)
    assert server._book_continue("p1") == {"id": "p3", "position": 0, "part": 3, "parts": 3}


def test_two_numbered_titles_in_one_folder_stay_apart(monkeypatch, tmp_path):
    # Grok a10r18 (high): the scanner fills a missing album with the folder name and takes a
    # leading number as track_no, so "01 Dune.m4b" (track 1) and "02 Foundation.m4b" (track 2)
    # looked like one book. A bare number + title is not a chapter name.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "_INDEX", {
        # The scanner fills a missing album with the folder name and marks it: numbers it also
        # invented prove nothing (grok a10r19).
        "dune": {"id": "dune", "path": "/Audiobooks/01 Dune.m4b", "album": "Audiobooks",
                 "genre": "Audiobook", "track_no": 1, "album_from_folder": True},
        "found": {"id": "found", "path": "/Audiobooks/02 Foundation.m4b", "album": "Audiobooks",
                  "genre": "Audiobook", "track_no": 2, "album_from_folder": True}})
    store.set_resume(items.local_id("found"), 3600)
    assert server._book_continue("dune") == {}


def test_dotted_numbered_titles_stay_apart(monkeypatch, tmp_path):
    # Grok a10r19 (high): "01. Dune" / "02. Foundation" got through the chapter-name rule because
    # "." was in the separator class. With a folder-name album, invented numbers decide nothing.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "_INDEX", {
        "dune": {"id": "dune", "path": "/Audiobooks/01. Dune.m4b", "album": "Audiobooks",
                 "genre": "Audiobook", "track_no": 1, "album_from_folder": True},
        "found": {"id": "found", "path": "/Audiobooks/02. Foundation.m4b", "album": "Audiobooks",
                  "genre": "Audiobook", "track_no": 2, "album_from_folder": True}})
    store.set_resume(items.local_id("found"), 3600)
    assert server._book_continue("dune") == {}


def test_bare_numeric_titles_are_not_one_book(monkeypatch, tmp_path):
    # Grok a10r19: "1984.m4b" and "2001.m4b" both collapse to "#", so a folder of numeric titles
    # continued across books. A shared name must keep at least one letter.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "_INDEX", {
        "a": {"id": "a", "path": "/Audiobooks/1984.m4b", "album": "Audiobooks",
              "genre": "Audiobook", "album_from_folder": True},
        "b": {"id": "b", "path": "/Audiobooks/2001.m4b", "album": "Audiobooks",
              "genre": "Audiobook", "album_from_folder": True}})
    store.set_resume(items.local_id("b"), 3600)
    assert server._book_continue("a") == {}


def test_an_upgraded_index_without_the_marker_does_not_merge_books(monkeypatch, tmp_path):
    # Claude a10r20: alpha.6-9 indexes have no album_from_folder key and are not re-read when
    # size+mtime match, so the round-19 fix never applied to upgraded installs.
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_STATE", None)
    monkeypatch.setattr(library, "_kinds_map", lambda: {})
    monkeypatch.setattr(library, "_INDEX", {
        "dune": {"id": "dune", "path": "/Audiobooks/01 Dune.m4b", "album": "Audiobooks",
                 "genre": "Audiobook", "track_no": 1},          # no marker: an old index
        "found": {"id": "found", "path": "/Audiobooks/02 Foundation.m4b", "album": "Audiobooks",
                  "genre": "Audiobook", "track_no": 2}})
    store.set_resume(items.local_id("found"), 10800)
    assert server._book_continue("dune") == {}
