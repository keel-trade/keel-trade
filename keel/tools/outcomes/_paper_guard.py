"""The paper/live control guard, both ways (paper-trading-v2 S06 §3.6, IC-06-2).

The paper routes 404 a ``real`` id and the live routes 404 a ``practice`` id,
with zero writes (S01 IC-01-9), so the happy path needs no pre-read. On a 404
the control tool reads the deployment's detail ONCE, with the agent-telemetry
marker (``X-Keel-Agent-Telemetry: 1``, through ``KeelClient.get``'s
``headers`` keyword, §3.5.1): a guard read is never an owner visit. If the row
exists and its venue block names the OTHER class, the caller gets
``invalid_target`` naming the other tool; otherwise the original 404 stands.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError, NotFoundError


#: The marker IC-01-9 excludes from the owner-visit refresh (S06 §3.5.1).
AGENT_TELEMETRY_HEADERS: dict[str, str] = {"X-Keel-Agent-Telemetry": "1"}

#: capital_class → the tool that controls that class.
_CONTROL_TOOL = {"real": "keel_live_control", "practice": "keel_paper_control"}


def other_class_refusal(client: Any, deployment_id: str, *, this_class: str) -> KeelError | None:
    """``invalid_target`` when ``deployment_id`` is a run of the other class.

    ``this_class`` is the class the calling tool controls (``"real"`` for
    ``keel_live_control``, ``"practice"`` for ``keel_paper_control``).
    Returns ``None`` when the row does not exist (the 404 stands) or is of
    this tool's own class; the caller then re-raises its original 404.
    """
    if this_class not in _CONTROL_TOOL:
        raise ValueError(f"unknown capital class {this_class!r}")
    try:
        row = client.get(f"/v1/deployments/{deployment_id}", headers=AGENT_TELEMETRY_HEADERS)
    except NotFoundError:
        return None
    venue = row.get("venue") if isinstance(row, dict) else None
    row_class = venue.get("capital_class") if isinstance(venue, dict) else None
    if row_class not in _CONTROL_TOOL or row_class == this_class:
        return None
    other_tool = _CONTROL_TOOL[row_class]
    kind = "a paper (simulated) run" if row_class == "practice" else "a live deployment"
    return KeelError(
        f"{deployment_id} is {kind}; {_CONTROL_TOOL[this_class]} does not control it.",
        error_code="invalid_target",
        exit_code=2,
        suggestion=f"use {other_tool}",
    )


def paper_server_error(e: KeelError, *, suggestion: str, keep_message: bool) -> KeelError | None:
    """A 5xx from a ``/v1/paper`` route, with paper advice (S10 review F3).

    ``None`` when ``e`` is not a server error. ``keep_message`` keeps the
    SDK's write-unconfirmed message (a create: a resend may start a second
    run). Otherwise the message is keel-api's own sentence when it sent one
    (the paper control routes answer a 503 with fixed prose, e.g. "The run is
    paused safely; retry the same update."), and the error is retryable only
    when that sentence says so. The code stays ``server_error``.
    """
    if e.error_code != "server_error":
        return None
    server = getattr(e, "server_message", None)
    if keep_message or not server:
        message, retryable = str(e), False if keep_message else e.retryable
    else:
        message, retryable = server, "retry" in server.lower()
    err = KeelError(
        message, error_code="server_error", exit_code=1, retryable=retryable, suggestion=suggestion
    )
    err.server_message = server
    return err
