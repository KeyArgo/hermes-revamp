"""Static directory symlinks must not escape the served root."""

import pytest


def test_directory_index_symlink_cannot_escape(server, monkeypatch, tmp_path):
    from revamp_core import server as srv

    root = tmp_path / 'web'
    (root / 'sub').mkdir(parents=True)
    secret = tmp_path / 'secret.html'
    secret.write_text('private data', encoding='utf-8')
    try:
        (root / 'sub' / 'index.html').symlink_to(secret)
    except OSError:
        pytest.skip('symlinks are unavailable')
    monkeypatch.setattr(srv, 'WEB_DIR', root)
    status, _, body = server.client.get('/web/sub/', raw=True)
    assert status == 404
    assert b'private data' not in body
