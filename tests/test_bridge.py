"""The player page's bridge key: in the page, and the same one from the Desktop proxy's /info."""

from __future__ import annotations

import hashlib
import hmac
import re


def _key(token: str) -> str:
    return hmac.new(token.encode(), b"revamp-bridge", hashlib.sha256).hexdigest()


def test_page_carries_the_bridge_key(server):
    code, _, raw = server.client.get("/", raw=True)
    html = raw.decode() if isinstance(raw, bytes) else str(raw)
    m = re.search(r'<meta name="revamp-bridge-key" content="([0-9a-f]{64})">', html)
    assert code == 200 and m, "the page must carry the bridge key"
    assert m.group(1) == _key(server.token)
    assert server.token not in m.group(1)  # derived, never the token itself


def test_key_is_not_the_token():
    assert _key("abc") != "abc" and len(_key("abc")) == 64
