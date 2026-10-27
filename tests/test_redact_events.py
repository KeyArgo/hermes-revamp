"""Release blocker found by Codex (2026-10-25): credentials in agent previews reached the
state file and the ticker from some harness paths. The server now masks every event."""
from revamp_core import redact, store


def test_mask_hides_keyword_and_shape_credentials():
    cases = {
        "curl -H 'Authorization: Bearer abc123def456' https://x": "abc123def456",
        "export OPENAI_API_KEY=sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA": "sk-proj-AAAA",
        "git clone https://bob:hunter2@github.com/x/y": "hunter2",
        "gh auth login --with-token ghp_abcdefghijklmnopqrstuvwxyz0123": "ghp_abcdefghij",
        "mysql --password=s3cr3t -u root": "s3cr3t",
        "aws configure set aws_access_key_id AKIAABCDEFGHIJKLMNOP": "AKIAABCDEFGHIJKLMNOP",
    }
    for text, secret in cases.items():
        assert secret not in redact.mask(text), text
    assert redact.mask("ls -la /tmp") == "ls -la /tmp"


def test_every_stored_agent_event_is_masked(tmp_path, monkeypatch):
    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    store._STATE = None
    ev = store.add_agent_event({"kind": "approval_request", "source": "claude-code",
                                "preview": "curl -u admin:Pa55word https://h -H 'X-Api-Key: zzzz9999'"})
    assert "Pa55word" not in ev["preview"] and "zzzz9999" not in ev["preview"]
    assert all("zzzz9999" not in str(e) for e in store.agent_events())
