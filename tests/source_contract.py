"""The contract every source must pass, against a running server.

browse -> item -> play URL (Range fetch) -> save progress -> read it back.
A new source's tests register it (with a fake upstream under tests/fakes/ if
it talks to a server) and call :func:`check_source`.
"""

from __future__ import annotations

import urllib.parse


def check_source(client, instance: str, browse_path: str = "", want_bytes: bool = True) -> dict:
    code, _, info = client.get(f"/api/sources/{instance}")
    assert code == 200 and info["id"] == instance, info
    code, _, listing = client.get(f"/api/sources/{instance}/browse/{browse_path}".rstrip("/"))
    assert code == 200, listing
    assert listing["items"], "browse returned nothing"
    item = listing["items"][0]
    # canonical shape
    for k in ("id", "source", "kind", "title", "playback", "capabilities"):
        assert k in item, (k, item)
    assert item["id"].startswith(instance + ":"), item["id"]
    assert item["source"] == instance
    assert item["playback"]["backend"] in ("webamp", "focus", "spotify")
    # item round trip
    media_id = item["id"].split(":", 1)[1]
    code, _, again = client.get(f"/api/sources/{instance}/item/" + urllib.parse.quote(media_id, safe=""))
    assert code == 200 and again["id"] == item["id"], again
    # audio: a Range request gets a 206 with exactly those bytes
    if want_bytes and item["playback"]["backend"] == "webamp":
        url = item["playback"]["url"]
        assert url.startswith("/api/"), "play URLs stay on Revamp (no upstream tokens in the page)"
        code, headers, body = client.get(url, headers={"Range": "bytes=0-9"}, raw=True)
        assert code == 206, code
        assert len(body) == 10
        assert headers.get("Content-Range", "").startswith("bytes 0-9/")
    # progress, keyed by the item's resumeKey (or id)
    key = item.get("resumeKey") or item["id"]
    code, _, _ = client.post("/api/resume", {"key": key, "position": 321})
    assert code == 200
    code, _, got = client.get("/api/resume?key=" + urllib.parse.quote(key, safe=""))
    assert code == 200 and got["position"] == 321, got
    client.post("/api/resume", {"key": key, "position": None})
    return item
