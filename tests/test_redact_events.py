"""Release blocker found in review (2026-10-25): credentials in agent previews reached the
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


def test_nested_event_headers_and_arguments_are_masked():
    event = {
        "preview": {
            "headers": {"Authorization": "Bearer secret-token"},
            "args": ["--token=secret-token"],
        }
    }
    assert "secret-token" not in repr(redact.mask_event(event))


def test_hook_masks_complete_values_before_persisting(tmp_path, monkeypatch):
    import __init__ as plugin

    monkeypatch.setenv("REVAMP_HOME", str(tmp_path))
    monkeypatch.setattr(store, "_STATE", None)
    events = []

    def publish(kind, fields):
        events.append(store.add_agent_event({"kind": kind, **fields}))

    monkeypatch.setattr(plugin, "_post_agent_event", publish)
    for arg in ('--password="fixture with spaces"', "--token 'fixture with spaces'",
                "api_key: fixture", "--secret=fixture", '--pwd="' + "fixture " * 20 + '"'):
        command = "deploy " + arg + " done"
        plugin._hook_pre_tool_call(args={"command": command})
        plugin._hook_pre_approval_request(command=command)
        for event in events[-2:]:
            assert "fixture" not in event["preview"]
            assert event["preview"].endswith(" done")
        assert "fixture" not in store._path().read_text()


def test_mask_scrubs_partially_masked_quoted_values():
    # Older senders sometimes replace the separator and leave the value.
    assert "fixture" not in redact.mask('deploy --password***"fixture with spaces"')


def test_quoted_user_credentials_and_shell_escaped_values():
    for command in ('curl --user="bob:fixture with spaces" done',
                    r"deploy --token=first\ fixture done",
                    'deploy --password="fixture' + "\\"):
        assert "fixture" not in redact.mask(command)
    assert redact.mask("deploy --password=basic done").endswith("=*** done")
