"""The position-layer upgrade facts every agent surface shares (Q-2448).

ONE owner for three things the MCP/CLI tools say about a strategy that pins
deprecated position components (position-layer spec 04 §2.3, §2.5):

* **which tool upgrades it** — :func:`upgrade_route`. ``keel_strategy_upgrade``
  where this server serves it; on a profile without it (the listed profile,
  D-48's fallback) ``keel_strategy_compose``, whose validation result carries
  the upgraded source (``POSITION_UPGRADE_AVAILABLE``'s ``result_source``).
* **the deprecation line** — :func:`deprecations_line`, the ``upgrade:`` DATA
  line a strategy view carries (04-R24). Facts only: no banner (D-29).
* **the deploy / update / resume refusal** — :func:`known_issue_refusal`
  maps keel-api's 422 ``KNOWN_ISSUE_UPGRADE_REQUIRED`` (D-42: "upgrade this
  strategy first", no acknowledgement, no override) to one
  ``KeelError(error_code="known_issue_upgrade_required")`` naming the route.
* **the lock an upgraded text is saved at** — :func:`upgrade_save_lock`. The
  planner can move a pin (``lock_changes``: a rule op whose pinned version is
  not certified on trade values, e.g. Crossover v1 -> v2), and its text is
  valid ONLY under its evolved lock. A save that sends the stored pins (push,
  compose) re-evolves them and keeps the old pin, so a save whose text IS the
  planner's upgrade of HEAD carries the planner's lock instead (Q-2448).
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError


#: keel-api's gate code (spec 04-R32; minted once in the rule catalog, P03-11).
KNOWN_ISSUE_CODE = "KNOWN_ISSUE_UPGRADE_REQUIRED"
#: The envelope ``code`` the MCP/CLI live tools raise for it (04-R34).
KNOWN_ISSUE_ERROR_CODE = "known_issue_upgrade_required"
#: The validator's per-group upgrade issue (04-R9).
UPGRADE_ISSUE_CODE = "POSITION_UPGRADE_AVAILABLE"
UPGRADE_TOOL = "keel_strategy_upgrade"
COMPOSE_TOOL = "keel_strategy_compose"


def upgrade_tool_served() -> bool:
    """Whether ``keel_strategy_upgrade`` is on this server's surface.

    The listed profile serves exactly ``LISTED_PROFILE_TOOLS``; every other
    profile serves every registered tool (subject to toolsets, which the
    ``backtest`` toolset of the upgrade tool shares with compose).
    """
    from ._toolsets import LISTED_PROFILE_TOOLS, is_listed_profile

    return not is_listed_profile() or UPGRADE_TOOL in LISTED_PROFILE_TOOLS


def upgrade_route() -> str:
    """The text that names how to upgrade, on THIS profile."""
    if upgrade_tool_served():
        return UPGRADE_TOOL
    return f"{COMPOSE_TOOL} carries the upgraded source"


def _issue_text(issue: Any) -> str | None:
    if not isinstance(issue, dict) or not issue.get("id"):
        return None
    summary = str(issue.get("summary") or "").strip()
    return f"known issue {issue['id']}: {summary}" if summary else f"known issue {issue['id']}"


def deprecations_line(deprecations: Any) -> str | None:
    """``TrailingStopExit v1, PositionStateMachine v2 are deprecated (known issue Q-2448: …); keel_strategy_upgrade``.

    ``deprecations`` is keel-api's derived ``[{component, version,
    known_issue, replacement_text}]`` (04-R24). ``None`` when there is
    nothing deprecated — no key, no claim.
    """
    if not isinstance(deprecations, list):
        return None
    rows = [d for d in deprecations if isinstance(d, dict) and d.get("component")]
    if not rows:
        return None
    names = ", ".join(
        f"{d['component']} v{d['version']}" if d.get("version") is not None else str(d["component"])
        for d in rows
    )
    verb = "is" if len(rows) == 1 else "are"
    issues: list[str] = []
    for d in rows:
        text = _issue_text(d.get("known_issue"))
        if text and text not in issues:
            issues.append(text)
    note = f" ({'; '.join(issues)})" if issues else ""
    if upgrade_tool_served():
        return f"{names} {verb} deprecated{note}; {UPGRADE_TOOL}"
    return f"{names} {verb} deprecated{note}; {COMPOSE_TOOL} carries the upgraded source"


def _refusal_payload(exc: BaseException) -> dict | None:
    """keel-api's ``detail.error`` object when ``exc`` is the known-issue gate.

    The 04-R32 body is ``{"detail": {"detail": <text>, "error": {...}}}``;
    the client keeps everything but the human text as ``KeelError.detail``,
    so the gate's machine half is ``detail["error"]``. A body already
    flattened to ``{"code": ...}`` is accepted too.
    """
    detail = getattr(exc, "detail", None)
    if not isinstance(detail, dict):
        return None
    error = detail.get("error")
    if isinstance(error, dict) and error.get("code") == KNOWN_ISSUE_CODE:
        return error
    if detail.get("code") == KNOWN_ISSUE_CODE:
        return detail
    return None


def known_issue_refusal(exc: BaseException, *, strategy_id: str | None = None) -> KeelError | None:
    """The live tools' refusal for a known-issue deploy/update/resume, or ``None``.

    ``None`` means ``exc`` is not the known-issue gate and the caller keeps
    its own handling. No parameter of any live tool can override the gate
    (D-42), so the suggestion names only the upgrade.
    """
    payload = _refusal_payload(exc)
    if payload is None:
        return None
    sid = payload.get("strategy_id") or strategy_id
    issues = [
        _issue_text(i)
        for i in payload.get("known_issues") or []
        if isinstance(i, dict) and i.get("id")
    ]
    action = str(payload.get("action") or "deploy")
    components = ", ".join(
        f"{c.get('name')} v{c.get('version')}"
        for c in payload.get("components") or []
        if isinstance(c, dict) and c.get("name")
    )
    message = (
        f"{action.capitalize()} refused: this strategy pins components with a known issue"
        + (f" ({components})" if components else "")
        + (f" — {'; '.join(i for i in issues if i)}" if issues else "")
        + ". Upgrade this strategy first, save the upgrade as a new version, then "
        + f"{action} that version."
    )
    if upgrade_tool_served():
        suggestion = (
            f"{UPGRADE_TOOL} rewrites the strategy into the TradeManager form"
            + (f" (strategy_id={sid})" if sid else "")
            + "; apply=true saves it as a new version."
        )
    else:
        suggestion = (
            f"{COMPOSE_TOOL} with dry_run=true returns the upgraded source in its "
            f"{UPGRADE_ISSUE_CODE} issue; saving that source makes the new version."
        )
    err = KeelError(
        message,
        error_code=KNOWN_ISSUE_ERROR_CODE,
        exit_code=1,
        suggestion=suggestion,
        retryable=False,
    )
    err.detail = dict(payload)
    if upgrade_tool_served() and sid:
        err.recovery_tool = UPGRADE_TOOL
        err.recovery_tool_args = {"strategy_id": sid}
    return err


def _same_text(a: str, b: str) -> bool:
    return a.rstrip("\n") == b.rstrip("\n")


def upgrade_save_lock(
    base_source: str | None, base_lock: dict[str, int] | None, new_source: str | None
) -> dict[str, Any] | None:
    """The planner's lock for saving ``new_source``, when it needs one.

    ``{"component_lock", "lock_changes"}`` when ``new_source`` IS the planner's
    upgrade of ``base_source`` at ``base_lock`` (every assisted group kept —
    exactly the ``result_source`` the validator's ``POSITION_UPGRADE_AVAILABLE``
    issue carries, and keel_strategy_upgrade's mechanical text) AND that
    upgrade moved a pin. ``None`` otherwise, so every other save — including
    an upgrade that moves no pin — sends exactly what it sent before.

    Recognition, not inference: the one engine re-plans the base and the texts
    must be equal, so a hand edit never borrows a moved pin. A text written
    from answered assisted questions is not recognised here; that path is
    ``keel_strategy_upgrade`` ``apply=true``, which saves the lock itself.
    """
    if not base_source or not base_lock or not new_source or _same_text(base_source, new_source):
        return None
    try:
        from pipeline_engine.dsl import upgrade
    except ImportError:
        # A wheel without the vendored planner cannot have produced a planner
        # text locally; the save keeps its pins (keel_strategy_upgrade raises
        # the build error for the upgrade itself).
        return None
    if not any(upgrade.recipe_for(name) is not None for name in base_lock):
        return None
    from keel.tools.local import _ensure_registry

    _ensure_registry()
    lock = {str(k): int(v) for k, v in base_lock.items()}
    try:
        out = upgrade.upgrade_strategy_source(base_source, lock, None)
        if out.get("source") is None:
            keep = [
                f"{q['key']}=keep"
                for g in out.get("groups") or []
                if g.get("mode") == "assisted"
                for q in g.get("questions") or []
            ]
            if not keep:
                return None
            out = upgrade.upgrade_strategy_source(base_source, lock, keep)
    except (upgrade.UpgradeAnswerError, upgrade.UpgradeVerificationError):
        return None
    result = out.get("source")
    changes = list(out.get("lock_changes") or [])
    if not isinstance(result, str) or not changes or not _same_text(result, new_source):
        return None
    return {"component_lock": dict(out["component_lock"]), "lock_changes": changes}


def head_upgrade_lock(
    client: Any, strategy_id: str, new_source: str, *, base_lock: dict[str, int] | None = None
) -> dict[str, Any] | None:
    """:func:`upgrade_save_lock` against the strategy's HEAD, best-effort.

    Reads ``/versions/HEAD/source`` (source and, unless ``base_lock`` is
    given, its component lock). A failed read is ``None``: the save then
    sends what it always sent, and keel-api's validation verdict says if the
    text needs another pin.
    """
    if base_lock is not None and not _pins_a_recipe_component(base_lock):
        return None
    try:
        head = client.get(f"/v1/strategies/{strategy_id}/versions/HEAD/source")
    except Exception:  # noqa: BLE001 — advisory read; the save keeps its pins
        return None
    if not isinstance(head, dict) or not isinstance(head.get("source"), str):
        return None
    lock = base_lock if base_lock is not None else head.get("component_lock")
    if not isinstance(lock, dict) or not lock:
        return None
    return upgrade_save_lock(head["source"], dict(lock), new_source)


def _pins_a_recipe_component(lock: dict[str, int]) -> bool:
    try:
        from pipeline_engine.dsl.upgrade import recipe_for
    except ImportError:
        return False
    return any(recipe_for(name) is not None for name in lock)


__all__ = [
    "COMPOSE_TOOL",
    "KNOWN_ISSUE_CODE",
    "KNOWN_ISSUE_ERROR_CODE",
    "UPGRADE_ISSUE_CODE",
    "UPGRADE_TOOL",
    "deprecations_line",
    "head_upgrade_lock",
    "known_issue_refusal",
    "upgrade_route",
    "upgrade_save_lock",
    "upgrade_tool_served",
]
