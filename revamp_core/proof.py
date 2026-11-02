"""Prove a listener is really REVamp before sending it the token.

Another local user (or program) can bind REVamp's port before it starts and answer
/api/health like REVamp. Clients therefore never send the token to a listener that has
not proved it already knows it: they send a random nonce, and only the real server can
return HMAC(token, nonce). The token itself never crosses to an unproven listener.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import urllib.parse
import urllib.request
from typing import Optional

_NONCE = re.compile(r"^[0-9a-f]{16,64}$")


def health_proof(token: str, nonce: str, port: int) -> str:
    # The port is part of the proof: a listener on another port can't relay the question
    # to the real REVamp and pass its answer off as its own.
    msg = f"revamp-health:{int(port)}:{nonce}".encode("ascii")
    return hmac.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def valid_nonce(nonce: str) -> bool:
    return isinstance(nonce, str) and bool(_NONCE.match(nonce))


def verify_listener(base: str, token: str, timeout: float = 2.0) -> bool:
    """True only if the server at *base* (http://127.0.0.1:<port>) proves it knows *token*.

    Callers check before every request that carries the token (not once per process):
    REVamp may have restarted and something else taken its port since."""
    if not token:
        return False
    try:
        port = urllib.parse.urlsplit(base).port
    except ValueError:
        return False
    if not port:
        return False
    nonce = secrets.token_hex(16)
    opener = local_opener()
    try:
        req = urllib.request.Request(f"{base}/api/health?nonce={nonce}", headers={"Accept": "application/json"})
        with opener.open(req, timeout=timeout) as resp:
            payload = json.loads(resp.read(4096).decode("utf-8-sig"))
    except Exception:
        return False
    proof: Optional[str] = payload.get("proof") if isinstance(payload, dict) else None
    return isinstance(proof, str) and hmac.compare_digest(proof, health_proof(token, nonce, port))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is never followed (it could lead elsewhere, token and all)."""

    def redirect_request(self, *a, **kw):  # type: ignore[no-untyped-def]
        return None


def local_opener() -> urllib.request.OpenerDirector:
    """For talking to REVamp on loopback with the token: no HTTP(S)_PROXY (a proxy would
    see the token) and no redirects."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
