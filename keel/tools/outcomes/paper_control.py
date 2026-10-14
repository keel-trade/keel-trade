"""`keel_paper_control` — pause, resume, stop, restart or update a paper (simulated) run.

paper-trading-v2 S06 §3.6 (IC-06-2). Paper lifecycle verbs get their own tool:
every paper write authorizes ``backtest.create`` (S01 §3.12, PT2-21), outside
the live-management actions, while ``keel_live_control`` declares
``runner.pause`` on the default-off ``live-write`` toolset and asserts local
arming. Folding paper in would force arming onto simulated actions.

Routes (S01 IC-01-9):
  - pause   → POST   /v1/paper/{id}/pause
  - resume  → POST   /v1/paper/{id}/resume
  - stop    → DELETE /v1/paper/{id}
  - restart → POST   /v1/paper/{id}/restart (201: the NEW run, plus
              ``restarted_from``, PT2-9)
  - update  → POST   /v1/paper/{id}/update, no body (S10 §4, PT2-90): moves a
              LIVE or PAUSED run onto the strategy's newest saved version
              (S01 §3.13); the run is relabelled ``v{N}`` and keeps its id
              and record, no release tag is minted (SOAK-S2), and the
              server's artifact gates apply. The result carries ``version``.

No ``trigger`` (S02's eager first eval fires on deploy). No local arming. A resume or restart
refused for the plan's paper slots relays §3.2.4b's neutral quota envelope
with ``blocked_action: "paper_control"``. On a 404 the guard reads the
detail once with the telemetry marker and answers ``invalid_target`` naming
``keel_live_control`` for a live deployment (``_paper_guard``); an id that is
no run of this org gets paper advice (list the paper runs), never the generic
strategy/commit/backtest hint (QA-W4). A refused transition (409: stop on a
stopped run, pause on a paused one, resume on a running one) is answered in
paper words with ``paper_run_state_conflict``, never live's "pull, then retry"
(QA-W4). An update onto the version the run already runs is
``paper_run_up_to_date``; a version the server's known-issue gate refuses is
``known_issue_upgrade_required`` (the live update's mapping, D-42); every
other server refusal (validation, a 503 with the run paused safely) passes
through with the server's own message.

On the listed profile (served with the listed flag on, S10 §3) every pointer
names a listed tool only: run ids come from ``keel_live_monitor``, and a
real-capital id is refused in listed words with the web app as the place it
is managed (``_paper_strings``' CONTROL_* rows).

Every outcome carries the paper label (``MONITOR_PAPER_LABEL``, QA-W3), and
every status it relays is keel-app's word for it (Deploying / Paper / Paused /
Stopped), never the raw ``LIVE`` (QA-W2).

Registers only while the paper SDK is released (``_PAPER_MODULES``, S06
§3.11 item 1, PT2-28).
"""

from __future__ import annotations

import re
from typing import Any

from keel.errors import (
    PAPER_NOT_ENABLED_CODE,
    ConflictError,
    EntitlementError,
    KeelError,
    NotFoundError,
    ValidationError,
)

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._paper_guard import other_class_refusal, paper_server_error
from ._paper_status import paper_status_word
from ._paper_strings import (
    CONTROL_REAL_ID_REFUSAL,
    CONTROL_REAL_ID_SUGGESTION,
    CONTROL_RUNS_POINTER,
    MONITOR_PAPER_LABEL,
)
from ._toolsets import is_listed_profile
from .open_in_app import app_url_for


# action → (method, path suffix)
_ACTIONS: dict[str, tuple[str, str]] = {
    "pause": ("POST", "/pause"),
    "resume": ("POST", "/resume"),
    "stop": ("DELETE", ""),
    "restart": ("POST", "/restart"),
    "update": ("POST", "/update"),
}

#: The actions, in the order every description and the enum name them.
ACTIONS: tuple[str, ...] = ("pause", "resume", "stop", "restart", "update")

# The status each action leaves the run in, when the route's answer carries
# none (a stop answers 204). A restart ends this record and starts a fresh
# run; the state named is the new run's. Rendered through keel-app's words.
_NEW_STATUS: dict[str, str] = {
    "pause": "PAUSED",
    "resume": "LIVE",
    "stop": "STOPPED",
    "restart": "LIVE",
    # An update restores the status the run had (LIVE or PAUSED); the route
    # always answers with the row, so this is only the documented default.
    "update": "LIVE",
}

#: The machine code of a refused transition (QA-W4).
PAPER_RUN_STATE_CONFLICT_CODE = "paper_run_state_conflict"
#: The machine code of an update onto the version the run already runs (S10 §4).
PAPER_RUN_UP_TO_DATE_CODE = "paper_run_up_to_date"
#: keel-api's sentence for it (routers/paper.py, update_paper_deployment).
_UP_TO_DATE_RE = re.compile(r"already at the latest version", re.IGNORECASE)

# keel-api's refused-transition sentences (strategy_registry/lifecycle.py and
# routers/paper.py's restart): the run's CURRENT status is the first capture.
_TRANSITION_RES = (
    re.compile(r"Invalid deployment transition: ([A-Z_]+) -> [A-Z_]+"),
    re.compile(r"Cannot restart a paper run in status '([A-Z_]+)'"),
    re.compile(r"Cannot update a paper run in status '([A-Z_]+)'"),
)

# What the call meant, in the sentence's words.
_ACTION_PHRASE: dict[str, str] = {
    "pause": "paused",
    "resume": "resumed",
    "stop": "stopped",
    "restart": "restarted",
    "update": "updated",
}

_READ_STATE = "`keel_live_monitor` reads the run's current state."


def _runs_pointer() -> str:
    """Where this org's paper run ids are listed, in this profile's tools."""
    return CONTROL_RUNS_POINTER.for_profile() or ""


def _listed_real_id_refusal(deployment_id: str) -> KeelError:
    """The guard's refusal of a real-capital id, in listed words (S10 §4.3).

    The full profile's text names ``keel_live_control``, which the listed
    connector does not serve; the listed connector has no live write path at
    all, so the suggestion is the web app (``keel_app_link`` is listed).
    """
    return KeelError(
        (CONTROL_REAL_ID_REFUSAL.form(listed=True) or "").format(deployment_id=deployment_id),
        error_code="invalid_target",
        exit_code=2,
        suggestion=CONTROL_REAL_ID_SUGGESTION.form(listed=True),
    )


def _up_to_date(deployment_id: str) -> KeelError:
    """An update onto the version the run already runs (S10 §4): nothing moved."""
    return KeelError(
        f"Paper run {deployment_id} (simulated) already runs the strategy's newest saved "
        "version; nothing was changed.",
        error_code=PAPER_RUN_UP_TO_DATE_CODE,
        exit_code=2,
        suggestion=(
            "Nothing to do. `update` moves a run onto a newer saved version: save one "
            "with `keel_strategy_compose`, then update."
        ),
        input={"deployment_id": deployment_id, "action": "update"},
    )


def _status_fields(row: Any) -> Any:
    """``row`` with its ``status`` in keel-app's words (QA-W2).

    The CLI and the local server keep the route's whole row. On the listed
    profile the row is the listed deployment allow-list with the paper fields
    (``_listed_projection.listed_deployment_fields``, Q-2268): no org,
    account, config or storage identifiers reach a directory connector.
    """
    if not isinstance(row, dict):
        return row
    if is_listed_profile():
        from ._listed_projection import listed_deployment_fields, pick

        row = pick(row, listed_deployment_fields(paper=True))
    if "status" not in row:
        return row
    word = paper_status_word(
        row.get("status"), health=row.get("health"), pause_reason=row.get("pause_reason")
    )
    return {**row, "status": word}


def _new_state(action: str, result: Any) -> str:
    """keel-app's word for where the action left the run: the route's own row
    when it sent one, else the action's documented outcome."""
    if isinstance(result, dict) and result.get("status"):
        return paper_status_word(
            result.get("status"),
            health=result.get("health"),
            pause_reason=result.get("pause_reason"),
        )
    return paper_status_word(_NEW_STATUS[action])


def _current_status(message: str) -> str | None:
    for pattern in _TRANSITION_RES:
        match = pattern.search(message)
        if match:
            return match.group(1)
    return None


def _state_conflict(e: ConflictError, deployment_id: str, action: str) -> KeelError:
    """A refused transition, in paper words (QA-W4).

    keel-api answers ``Invalid deployment transition: STOPPED -> STOPPED`` (or
    the restart route's ``Cannot restart a paper run in status 'STOPPED'``),
    and the SDK's generic 409 advice is live's "pull, then retry", which no
    paper run needs. The run's status is read off the sentence; a sentence
    without one keeps keel-api's text with the read-the-run advice.
    """
    if action == "update" and _UP_TO_DATE_RE.search(str(e)):
        return _up_to_date(deployment_id)
    current = _current_status(str(e))
    if current is None:
        err = KeelError(
            str(e),
            error_code=PAPER_RUN_STATE_CONFLICT_CODE,
            exit_code=2,
            suggestion=f"Nothing was changed. {_READ_STATE}",
        )
        err.detail = getattr(e, "detail", None)
        return err
    word = paper_status_word(current)
    if current == "STOPPED":
        message = (
            f"Paper run {deployment_id} (simulated) is already stopped; nothing was changed."
            if action == "stop"
            else (
                f"Paper run {deployment_id} (simulated) is stopped and cannot be "
                f"{_ACTION_PHRASE[action]}; nothing was changed."
            )
        )
        suggestion = (
            "Nothing to do: a stopped paper run stays stopped and its record is kept. "
            "To run the strategy again, start a new paper run with `keel_paper_deploy`."
        )
    elif current == "PAUSED" and action == "pause":
        message = f"Paper run {deployment_id} (simulated) is already paused; nothing was changed."
        suggestion = "Nothing to do. `action='resume'` restarts its evaluations."
    elif current == "LIVE" and action == "resume":
        message = (
            f"Paper run {deployment_id} (simulated) is already running ({word}); "
            "nothing was changed."
        )
        suggestion = f"Nothing to do. {_READ_STATE}"
    else:
        message = (
            f"Paper run {deployment_id} (simulated) is {word} and cannot be "
            f"{_ACTION_PHRASE[action]} now; nothing was changed."
        )
        suggestion = _READ_STATE
    err = KeelError(
        message,
        error_code=PAPER_RUN_STATE_CONFLICT_CODE,
        exit_code=2,
        suggestion=suggestion,
        input={"deployment_id": deployment_id, "action": action, "status": word},
    )
    return err


def _call(client: Any, method: str, path: str) -> Any:
    if method == "POST":
        return client.post(path)
    if method == "DELETE":
        return client.delete(path)
    raise KeelError(  # pragma: no cover — _ACTIONS is closed
        f"Internal error: unsupported method {method!r}.",
        error_code="internal_error",
        exit_code=1,
        suggestion="This is a bug in the SDK: the action table is closed.",
    )


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    deployment_id = str(args.get("deployment_id") or "").strip()
    action = str(args.get("action") or "").strip()
    if not deployment_id:
        raise KeelError(
            "Missing required `deployment_id` argument.",
            error_code="missing_deployment_id",
            exit_code=2,
            suggestion="Pass deployment_id as the positional argument.",
        )
    if action not in _ACTIONS:
        raise ValidationError(
            f"Unknown action {action!r}. Valid actions: {list(ACTIONS)}",
            suggestion=f"Pass `action` as one of: {', '.join(ACTIONS)}.",
        )

    method, suffix = _ACTIONS[action]
    client = ctx.get_client()
    try:
        result = _call(client, method, f"/v1/paper/{deployment_id}{suffix}")
    except EntitlementError as e:
        # The permission arm and the neutral quota wall (§3.2.4, §3.6).
        from ._paper_walls import paper_wall

        handoff = paper_wall(
            e,
            blocked_action="paper_control",
            retry_call={
                "tool": "keel_paper_control",
                "args": {"deployment_id": deployment_id, "action": action},
            },
        )
        if handoff is not None:
            raise handoff from e
        raise
    except NotFoundError as e:
        if e.error_code == PAPER_NOT_ENABLED_CODE:
            # Paper is off on this environment (§3.2.4 (c)): relayed as typed,
            # with no guard read (the row's class cannot change the answer).
            raise
        refusal = other_class_refusal(client, deployment_id, this_class="practice")
        if refusal is not None:
            if is_listed_profile():
                raise _listed_real_id_refusal(deployment_id) from e
            raise refusal from e
        # No run of this org has this id: paper advice, never the generic
        # strategies/commits/backtests hint (QA-W4).
        err = NotFoundError(str(e), suggestion=f"Check the id: {_runs_pointer()}")
        err.detail = getattr(e, "detail", None)
        raise err from e
    except ConflictError as e:
        if e.error_code != ConflictError.error_code:
            raise  # a coded conflict speaks for itself
        raise _state_conflict(e, deployment_id, action) from e
    except KeelError as e:
        # D-42 / 04-R34, as keel_live_update maps it: an update onto a version
        # that pins a known-issue component is refused until the strategy is
        # upgraded, and the refusal names the upgrade route on this profile.
        # Every other refusal propagates with the server's own text.
        # A 5xx keeps keel-api's own sentence ("The run is paused safely;
        # retry the same update.") with the paper read-back (S10 review F3).
        server = paper_server_error(e, suggestion=_READ_STATE, keep_message=False)
        if server is not None:
            raise server from e
        from ._known_issue import known_issue_refusal

        refusal = known_issue_refusal(e)
        if refusal is not None:
            raise refusal from e
        raise

    if action == "restart":
        new_id = str((result or {}).get("deployment_id") or "")
        if not new_id:
            raise KeelError(
                "keel-api answered a paper restart without the new run's deployment_id.",
                error_code="unexpected_response",
                exit_code=1,
                suggestion=f"The new run may have started: {_runs_pointer()}",
            )
        url = app_url_for("paper", new_id, ctx)
        return OutcomeResult(
            run_id=new_id,
            hero_url=url,
            share_url=None,
            extra={
                "label": MONITOR_PAPER_LABEL.for_profile(),
                "action": action,
                "new_state": _new_state(action, result),
                "deployment_id": new_id,
                "url": url,
                "venue": result.get("venue"),
                # The id this call restarted (PT2-9); the route echoes it too.
                "restarted_from": deployment_id,
                "result": _status_fields(result),
            },
        )

    url = app_url_for("paper", deployment_id, ctx)
    extra: dict[str, Any] = {
        "label": MONITOR_PAPER_LABEL.for_profile(),
        "action": action,
        "new_state": _new_state(action, result),
        "deployment_id": deployment_id,
        "url": url,
    }
    if action == "update":
        # The run's new label (``v{N}``, the target commit's sequence; SOAK-S2),
        # as the route sent it.
        extra["version"] = (result or {}).get("deployed_version_string")
    extra["result"] = _status_fields(result)
    return OutcomeResult(run_id=deployment_id, hero_url=url, share_url=None, extra=extra)


PAPER_CONTROL_DESCRIPTION = (
    "Pause, resume, stop, restart or update a paper (simulated) run. `stop` is permanent "
    "for that record: the record is kept and its line stops. `restart` ends this record "
    "and starts a fresh run of the same version with the same simulated capital, and "
    "returns the new run's id and URL. `update` moves a running or paused run onto the "
    "strategy's newest saved version: the run keeps its id and record, is relabelled "
    "with the new version, and the result carries `version`; an update onto the version "
    "it already runs is refused with paper_run_up_to_date. Stopping frees the run's paper "
    "slot; restarting reuses it. Each call changes the run once; a repeated call is "
    "refused with paper_run_state_conflict when the run is already in that state. This "
    "tool never touches live deployments: a live deployment is refused with "
    "invalid_target, and `keel_live_control` controls those. Get the `deployment_id` from "
    "`keel_paper_deploy` or `keel_deployments_list`; read the run with "
    "`keel_live_monitor`."
)

#: Listed profile (S10 §4, PT2-90): served only while the listed flag is on for
#: the caller's host. Third person, simulated in the first sentence, every
#: action named, only listed tools named, the neighbour stated as a fact
#: (Q-1804), and the live half in S06 §3.4.4's listed register.
PAPER_CONTROL_LISTED_DESCRIPTION = (
    "Pauses, resumes, stops, restarts or updates one simulated paper-trading run; no "
    "real orders are placed and no real capital is at risk. `update` moves the run onto "
    "the strategy's newest saved version and keeps its record; `restart` starts a fresh "
    "record of the same version; `stop` ends the record for good and frees its paper "
    "slot. A repeated call is refused when the run is already in that state. Running a "
    "strategy with real capital is a step you take in the Keel web app. The run's id "
    "comes from `keel_paper_deploy` or `keel_live_monitor`, which also reads the run."
)

_ACTION_DESCRIPTION = (
    "Lifecycle action: 'pause' (halt the schedule), 'resume' (re-enable it), 'stop' (end "
    "the run; permanent for this record), 'restart' (end this record and start a fresh "
    "run), 'update' (move the run onto the strategy's newest saved version)."
)


def _input_schema(deployment_id_description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["deployment_id", "action"],
        "properties": {
            "deployment_id": {"type": "string", "description": deployment_id_description},
            "action": {
                "type": "string",
                "enum": list(ACTIONS),
                "description": _ACTION_DESCRIPTION,
            },
        },
    }


PAPER_CONTROL_INPUT_SCHEMA = _input_schema(
    "Paper run to control. From `keel_paper_deploy` or `keel_deployments_list`."
)
#: The listed form names only listed tools (S06 §3.4.4).
PAPER_CONTROL_LISTED_INPUT_SCHEMA = _input_schema(
    "Paper run to control. From `keel_paper_deploy` or `keel_live_monitor`."
)


PAPER_CONTROL = register(
    OutcomeTool(
        name="keel_paper_control",
        required_action="backtest.create",
        cli_path=("paper", "control"),
        toolset="paper",
        # grounded-in: system/operating_core.md:116-125 (the `live` section:
        # live control is a separate, armed surface) + paper-trading-v2
        # specs/06 §3.6 (one action per paper write, no arming, the guard)
        # + specs/10 §4 (`update`, the listed form; PT2-90).
        description=PAPER_CONTROL_DESCRIPTION,
        listed_description=PAPER_CONTROL_LISTED_DESCRIPTION,
        input_schema=PAPER_CONTROL_INPUT_SCHEMA,
        listed_input_schema=PAPER_CONTROL_LISTED_INPUT_SCHEMA,
        annotations={
            "title": "Control a Paper Run (Simulated)",
            "readOnlyHint": False,
            # `stop` is irreversible for its record and `update` replaces the
            # version the run executes in place (OpenAI: "even in only select
            # modes"), so the tool is destructive and hosts confirm each call.
            "destructiveHint": True,
            "idempotentHint": False,
            # Bounded to the caller's own Keel workspace: a simulated run on
            # Keel's market data; it contacts no exchange account and publishes
            # nothing (the annotation policy, _annotation_justifications.py).
            "openWorldHint": False,
        },
        handler=_handler,
        confirm_in_cli=True,
    )
)
