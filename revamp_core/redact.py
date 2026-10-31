"""Hide credentials in agent-activity text before it is stored or shown.

Every harness (Hermes, Claude Code, Codex, pi, ...) reports tool previews such as shell
commands; a token typed on a command line or in a URL would otherwise sit in the state file
and scroll past on the ticker. The server applies this to every agent event, whatever sent it.
"""
from __future__ import annotations

import re

_KEYWORD = re.compile(
    r"((?:authorization|api[-_]?key|api[-_]?secret|access[-_]?key|client[-_]?secret|token|secret"
    r"|password|passwd|pwd|bearer|basic)[-_ ]?[=: ]*(?:bearer|basic)?[ \t]*)(?:[^\s'\"&]+)",
    re.IGNORECASE)
# Well-known credential shapes that appear without a keyword in front of them.
_SHAPES = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{30,}|hf_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,})\b")
# user:password@host in URLs
_URL_AUTH = re.compile(r"(\w+://[^/\s:@]+:)[^@\s/]+@")
# curl -u user:password / --user user:password
_USER_FLAG = re.compile(r"((?:^|\s)(?:-u|--user)[ =]+[^\s:'\"]+:)[^\s'\"]+")


def mask(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    try:
        text = _KEYWORD.sub(lambda m: f"{m.group(1)}***", text)
        text = _SHAPES.sub("***", text)
        text = _USER_FLAG.sub(lambda m: f"{m.group(1)}***", text)
        return _URL_AUTH.sub(lambda m: f"{m.group(1)}***@", text)
    except Exception:
        return text


def _mask_value(value):
    """Copy JSON-shaped event data while masking strings at every depth."""
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_mask_value(v) for v in value)
    return value


def mask_event(event: dict) -> dict:
    """A copy of *event* with every nested string field masked."""
    return _mask_value(event)
