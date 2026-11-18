"""What may go into a keel-api URL path (Q-3352).

Ids, names, slugs and version refs from callers (MCP tool arguments, MCP
resource URIs, CLI arguments) are interpolated into ``/v1/...`` paths. A
value that is not exactly one path segment re-routes the authenticated
request to a DIFFERENT keel-api endpoint: httpx removes ``.``/``..``
segments (``/v1/strategies/../deployments/x`` is ``/v1/deployments/x``),
``?`` and ``#`` end the path, and uvicorn percent-decodes ``%2F`` to ``/``
before routing. Percent-quoting the value is therefore NOT a fix on this
stack; the only correct behaviour is to refuse it.

One rule, two places it is applied:

* :func:`seg` — at every interpolation (``f"/v1/strategies/{seg(sid)}"``).
  It knows the segment boundary, so it also refuses a ``/`` inside a value.
  ``tests/test_api_path_guard.py`` fails if a ``/v1`` f-string interpolates
  anything without it.
* :func:`check_request_path` — in ``KeelClient._request``, the one door every
  SDK request passes, over the whole path: the backstop for a path built any
  other way.

Refusals are one plain sentence; they never repeat the value or the path.
"""

from __future__ import annotations

from keel.errors import UsageError


__all__ = ["check_request_path", "seg"]

#: Characters that change how a URL path is parsed or routed. Everything
#: else in a segment (letters, digits, ``_``, ``-``, ``.`` inside a tag like
#: ``v1.2``, even a space) is sent percent-encoded by httpx and decoded back
#: to the same text by keel-api, so it cannot move the request.
_STRUCTURAL = frozenset("/?#%\\")


def _bad_segment(text: str) -> bool:
    return (
        text in ("", ".", "..")
        or any(ch in _STRUCTURAL for ch in text)
        or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)
    )


def _refusal() -> UsageError:
    return UsageError(
        "An id or name in this request is not valid: ids are letters, digits, `_` and `-`.",
        suggestion="Pass the id exactly as a Keel result gave it.",
    )


def seg(value: object) -> str:
    """``value`` as ONE path segment, or ``UsageError``.

    Ints (sequence numbers, revisions) are accepted and rendered; anything
    else must be a string that is a single, non-dot, non-empty segment.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _refusal()
    text = str(value)
    if _bad_segment(text):
        raise _refusal()
    return text


def check_request_path(path: str) -> None:
    """Refuse a request path whose structure a value could have changed.

    The path must be absolute (``/...``) and every segment after the leading
    ``/`` must pass the same rule :func:`seg` applies.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        raise _refusal()
    if any(_bad_segment(part) for part in path[1:].split("/")):
        raise _refusal()
