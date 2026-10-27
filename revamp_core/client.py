"""Stdlib HTTP client for a running Revamp server.

``RevampClient(base=None, token=None)`` — defaults read from config/data dir.
Every method returns parsed JSON (or ``{}`` for empty bodies) and raises
``RevampUnavailable`` when the server can't be reached. 5 s timeout, urllib
only. Used by the Hermes tools in ``__init__.py``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .config import data_dir, setting


class RevampUnavailable(RuntimeError):
    """The Revamp HTTP server did not answer."""


def _default_token() -> str:
    try:
        return (data_dir() / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class RevampClient:
    """Thin JSON client; ``get/post/put/delete`` plus helpers."""

    def __init__(self, base: Optional[str] = None, token: Optional[str] = None,
                 timeout: float = 5.0) -> None:
        self.base = (base or f"http://127.0.0.1:{int(setting('port'))}").rstrip("/")
        self.token = token or _default_token()
        self.timeout = timeout

    # -- low level ----------------------------------------------------------

    def request(self, method: str, path: str, params: Optional[dict] = None,
                body: Optional[dict] = None) -> Any:
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header("X-Revamp-Token", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                detail = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                detail = {"raw": raw.decode("utf-8", errors="replace")}
            message = detail.get("error") if isinstance(detail, dict) else detail
            raise RuntimeError(f"revamp request returned HTTP {exc.code}: {message}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RevampUnavailable(f"revamp at {self.base} unreachable: {exc}") from exc
        except Exception as exc:  # timeout etc.
            raise RevampUnavailable(f"revamp request failed: {exc}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {"raw": raw.decode("utf-8", errors="replace")}

    # -- verbs --------------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, body: Optional[dict] = None) -> Any:
        return self.request("POST", path, body=body if body is not None else {})

    def put(self, path: str, body: Optional[dict] = None) -> Any:
        return self.request("PUT", path, body=body if body is not None else {})

    def delete(self, path: str, **params: Any) -> Any:
        return self.request("DELETE", path, params=params or None)

    # -- convenience --------------------------------------------------------

    def healthy(self) -> bool:
        try:
            info = self.get("/api/health")
            return bool(info) and info.get("app") == "revamp"
        except RevampUnavailable:
            return False

    def available(self) -> bool:
        return self.healthy()
