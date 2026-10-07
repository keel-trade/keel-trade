"""`keel_strategy_upgrade` — rewrite deprecated position components (Q-2448).

Position-layer spec 04-R21. Nine components are deprecated in place (D-02,
D-23) and keep running byte-identically; a strategy that pins one of the
five exits or ``StopDistanceRiskSizer`` carries known issue Q-2448 and is
refused at deploy / update / resume until it is upgraded (D-42). This tool is
that upgrade, on the CLI (``keel strategy upgrade <strategy_id>``) and MCP
alike — one verb, one envelope (the CLI/MCP parity principle).

It reads the strategy's HEAD source and component lock
(``/v1/strategies/{id}/versions/HEAD/source``) and runs the planner LOCALLY
(``pipeline_engine.dsl.upgrade.upgrade_strategy_source``, the one shared
engine every upgrade surface calls; vendored into the wheel by
``build_data.py``):

* ``apply=false`` (default): the plan per position group (``mechanical`` /
  ``assisted`` with scripted questions / ``manual``), the unified diff, the
  upgraded source and its validation. Nothing is written.
* ``apply=true`` with every question answered: one ``PATCH
  /v1/strategies/{id}`` carrying ``expected_source_hash`` (a 409 means HEAD
  moved after the read) — a NEW version; nothing is deleted and nothing is
  rewritten server-side (D-02). The result names ``previous_commit_id`` and
  ``commit_id``; comparing the two is ``keel_backtest_run`` on each, then
  ``keel_backtest_compare`` (D-29: no new comparison surface).

``answers`` items are ``<question_id>=<choice>[:<value>]`` (the CLI's
repeatable ``--answers``); ``keep`` leaves that group on the deprecated
components, running exactly as today.
"""

from __future__ import annotations

from typing import Any

from keel.errors import ConflictError, KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from .open_in_app import app_url_for


#: The default commit message of an applied upgrade (04-R21).
DEFAULT_MESSAGE = "Upgrade to TradeManager (position layer, Q-2448)"
#: The help topic a manual group points at (spec 05 owns its content).
MANUAL_HELP_TOPIC = "entry_exit_patterns"


def _engine():
    """The shared planner, or a build error naming the fix.

    No fallback (lessons: never fabricate a stand-in for a correctness
    path): a wheel without the vendored planner cannot upgrade, and says so.
    """
    try:
        from pipeline_engine.dsl import upgrade
    except ImportError as exc:  # pragma: no cover - exercised only on a stale wheel
        raise KeelError(
            "This keel-trade build does not carry the position-layer upgrade planner.",
            error_code="upgrade_unavailable",
            exit_code=2,
            suggestion=(
                "Upgrade keel-trade (`pipx upgrade keel-trade`); a source checkout "
                "regenerates the vendored engine with `PYTHONPATH=libs python "
                "packages/keel-trade/keel-sdk/scripts/build_data.py`."
            ),
        ) from exc
    return upgrade


def run_upgrade(
    source: str, component_lock: dict[str, int] | None, answers: list[str] | None
) -> dict[str, Any]:
    """The shared engine's result for one source (04-R21's return shape).

    Maps the planner's two structured refusals to ``KeelError`` with their
    own codes, lower-cased the way the envelope carries server codes:
    ``upgrade_answer_missing`` / ``upgrade_answer_invalid`` name the question;
    ``upgrade_verification_failed`` names the failed check and means nothing
    was produced (the planner never falls back to re-emitting the file).
    """
    upgrade = _engine()
    # The planner reads component signatures and the verification validates:
    # both need the bundled registry hydrated (the SDK registry_loader stub
    # refuses an empty one rather than validate against nothing).
    from keel.tools.local import _ensure_registry

    _ensure_registry()
    try:
        return upgrade.upgrade_strategy_source(source, component_lock or {}, answers or None)
    except upgrade.UpgradeAnswerError as exc:
        payload = exc.to_dict() if hasattr(exc, "to_dict") else {}
        err = KeelError(
            str(exc),
            error_code=str(payload.get("code") or "UPGRADE_ANSWER_INVALID").lower(),
            exit_code=2,
            suggestion=(
                "Answer each question as <question_id>=<choice>[:<value>] using one of "
                "the choices the dry run lists; `keep` leaves that part unchanged."
            ),
        )
        err.detail = payload or None
        raise err from exc
    except upgrade.UpgradeVerificationError as exc:
        payload = exc.to_dict() if hasattr(exc, "to_dict") else {}
        err = KeelError(
            str(exc),
            error_code="upgrade_verification_failed",
            exit_code=1,
            suggestion=(
                f'Nothing was changed. keel_help topic="{MANUAL_HELP_TOPIC}" shows the '
                "TradeManager form to write by hand; keel_strategy_compose saves it."
            ),
        )
        err.detail = payload or None
        raise err from exc


def pending_questions(groups: list[dict]) -> list[dict]:
    """Every question of every assisted group, flattened, with its group's path."""
    out: list[dict] = []
    for group in groups:
        if group.get("mode") != "assisted":
            continue
        for question in group.get("questions") or []:
            out.append({**question, "group": group.get("path")})
    return out


def _status(result: dict, answered: bool) -> str:
    groups = result.get("groups") or []
    if not groups:
        return "nothing_to_upgrade"
    if result.get("source") is not None:
        return "ready"
    if all(g.get("mode") == "manual" for g in groups):
        return "manual"
    return "ready" if answered else "needs_answers"


def _read_head(client: Any, strategy_id: str) -> dict:
    payload = client.get(f"/v1/strategies/{strategy_id}/versions/HEAD/source")
    if not isinstance(payload, dict) or not isinstance(payload.get("source"), str):
        raise KeelError(
            f"Strategy {strategy_id} has no readable HEAD source to upgrade.",
            error_code="source_unavailable",
            suggestion="keel_strategy_get with include_source=true shows what the server holds.",
        )
    return payload


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = (args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id`.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion="Pass the strategy to upgrade, e.g. `keel strategy upgrade str_abc`.",
        )
    raw_answers = args.get("answers") or []
    if isinstance(raw_answers, str):
        raw_answers = [raw_answers]
    answers = [str(a).strip() for a in raw_answers if str(a).strip()]
    apply = bool(args.get("apply", False))
    message = (args.get("message") or "").strip() or DEFAULT_MESSAGE

    client = ctx.get_client()
    head = _read_head(client, strategy_id)
    source: str = head["source"]
    lock = {str(k): int(v) for k, v in (head.get("component_lock") or {}).items()}
    result = run_upgrade(source, lock, answers)

    groups = list(result.get("groups") or [])
    upgraded = result.get("source")
    changed = isinstance(upgraded, str) and upgraded != source
    status = _status(result, bool(answers))
    body: dict[str, Any] = {
        "strategy_id": strategy_id,
        "status": status,
        "applied": False,
        "head_commit_id": head.get("commit_id"),
        "head_sequence": head.get("sequence_number"),
        "groups": groups,
        "questions": pending_questions(groups) if upgraded is None else [],
        "source": upgraded,
        "diff": result.get("diff") or "",
        "validation": result.get("validation"),
        "component_lock": result.get("component_lock"),
        # The pins the upgrade moves (Q-2448): the text is valid only at
        # component_lock, which apply=true saves with it.
        "lock_changes": list(result.get("lock_changes") or []),
    }

    if status == "nothing_to_upgrade":
        body["next"] = ["Nothing to upgrade: this version pins no deprecated position component."]
    elif status == "manual":
        body["next"] = [
            "No ready edit for this strategy's position shape; "
            f'keel_help topic="{MANUAL_HELP_TOPIC}" shows the TradeManager form, '
            "and keel_strategy_compose saves it."
        ]
    elif upgraded is None:
        body["next"] = [
            "Answer the questions (answers=['<question_id>=<choice>[:<value>]', ...]) "
            "and call again; `keep` leaves that part on the deprecated components."
        ]
    elif not changed:
        body["status"] = "unchanged"
        body["next"] = ["Every answer kept the deprecated components; nothing changes."]
    elif not apply:
        body["next"] = [
            "Dry run — nothing written. apply=true saves this upgrade as a new version."
        ]
    else:
        body.update(_apply(client, strategy_id, head, upgraded, result, message))

    return OutcomeResult(
        run_id=strategy_id,
        hero_url=app_url_for("strategy", strategy_id, ctx),
        share_url=None,
        extra=body,
    )


def _apply(
    client: Any, strategy_id: str, head: dict, upgraded: str, result: dict, message: str
) -> dict[str, Any]:
    patch: dict[str, Any] = {"source": upgraded, "message": message}
    if head.get("source_hash"):
        patch["expected_source_hash"] = head["source_hash"]
    new_lock = result.get("component_lock")
    if isinstance(new_lock, dict) and new_lock:
        # The evolved lock the planner verified against (04-R18: every pin of
        # a component still present is kept, so sizers keep their versions,
        # except a proven trade-op move listed in lock_changes). The text is
        # valid ONLY at this lock; never let keel-api re-evolve the stored one.
        patch["component_lock"] = new_lock
    try:
        saved = client.patch(f"/v1/strategies/{strategy_id}", json=patch)
    except ConflictError as exc:
        exc.suggestion = (
            "HEAD moved after this upgrade read it; nothing was written. Call "
            "keel_strategy_upgrade again to plan against the new HEAD."
        )
        raise
    saved = saved if isinstance(saved, dict) else {}
    previous = head.get("commit_id")
    commit_id = saved.get("head_commit_id")
    out: dict[str, Any] = {
        "applied": True,
        "status": "applied",
        "message": message,
        "previous_commit_id": previous,
        "commit_id": commit_id,
        "sequence": saved.get("current_sequence"),
        "server_validation": saved.get("validation"),
    }
    if saved.get("unchanged") is True:
        out["applied"] = False
        out["status"] = "unchanged"
        out["next"] = ["The server already held this source; no new version was written."]
        return out
    out["next"] = [
        f"Saved as a new version (commit_id={commit_id}); the previous one is {previous}.",
        f"keel_backtest_run with version={previous} and with version={commit_id}, "
        "then keel_backtest_compare on the two run ids.",
    ]
    return out


STRATEGY_UPGRADE = register(
    OutcomeTool(
        name="keel_strategy_upgrade",
        required_action="strategy.update",
        cli_path=("strategy", "upgrade"),
        toolset="backtest",
        # grounded-in: position-layer spec 04-R21 (one upgrade verb, MCP = CLI),
        # D-02 (never rewritten server-side; a save is a new version), D-29
        # (agent context only), D-42 (the deploy gate this unblocks).
        description=(
            "Upgrade a saved strategy that pins deprecated position components "
            "(PositionStateMachine, TradeLevelRiskExit, ScalingPositionManager, the "
            "stop / target / trailing / time exits, StopDistanceRiskSizer) into the "
            "TradeManager form — the edit the validator's POSITION_UPGRADE_AVAILABLE "
            "issue describes; a hand-written change is `keel_strategy_compose`. It "
            "reads the latest version and returns each position group's plan "
            "(mechanical, assisted with questions, or manual), the unified diff, the "
            "upgraded source and its validation; rule names and comments are kept. "
            "`answers` answers an assisted group's questions as "
            "`<question_id>=<choice>[:<value>]`, and `keep` leaves that group as it "
            "is. Without `apply` nothing is written; `apply=true` saves the upgrade as "
            "a new version, conflict-safe against the version read, and returns "
            "`previous_commit_id` and `commit_id`. A known-issue strategy is refused "
            "at deploy until it is upgraded. Comparing the two versions is "
            "`keel_backtest_run` on each, then `keel_backtest_compare`."
        ),
        input_schema={
            "type": "object",
            "required": ["strategy_id"],
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "x-cli-positional": True,
                    "description": "The strategy to upgrade (`str_*`); its latest version is read.",
                },
                "answers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Answers to an assisted group's questions, one per item: "
                        "`<question_id>=<choice>[:<value>]` (for example "
                        "`Q-VSEF=trade_peak`); `keep` leaves that group unchanged."
                    ),
                },
                "apply": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Save the upgrade as a new version. Default false: plan, diff "
                        "and validation only, nothing written."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        f'Commit message for the saved version (default "{DEFAULT_MESSAGE}").'
                    ),
                },
            },
        },
        annotations={
            "title": "Upgrade Deprecated Position Components",
            "readOnlyHint": False,
            "destructiveHint": False,  # a new version; nothing deleted
            "idempotentHint": False,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
