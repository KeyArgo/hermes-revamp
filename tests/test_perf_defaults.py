"""New installs leave the approval dip off. A later settings write merges."""

from __future__ import annotations

from revamp_core import config, store


def test_new_install_duck_is_off(server, monkeypatch):
    monkeypatch.delenv("REVAMP_DUCK_ON_APPROVAL", raising=False)
    _, _, state = server.client.get("/api/state")
    assert state["settings"]["duck_on_approval"] is False
    assert config.setting("duck_on_approval") is False


def test_saved_duck_choice_stays(server, monkeypatch):
    status, _, _ = server.client.put("/api/state", {"settings": {"duck_on_approval": True}})
    assert status == 200
    # Drop the in-memory cache so the next read is the file a returning user already has.
    monkeypatch.setattr(store, "_STATE", None)
    _, _, state = server.client.get("/api/state")
    assert state["settings"]["duck_on_approval"] is True


def test_settings_put_merges(server):
    status, _, _ = server.client.put("/api/state", {
        "settings": {"duck_on_approval": True, "ticker": False},
    })
    assert status == 200
    status, _, state = server.client.put("/api/state", {"settings": {"bookRate": 1.5}})
    assert status == 200
    settings = state["settings"]
    assert settings["duck_on_approval"] is True
    assert settings["ticker"] is False
    assert settings["bookRate"] == 1.5
    assert settings["duck_level"] == 0.25
