"""`keel_strategy_compose` — create, update, validate, or compile a strategy.

Replaces: `strategy_new`, `strategy_validate`, `strategy_compile`,
`strategy_explain`, `strategy_create`, `strategy_update`, `update_strategy`,
`strategy_push`, `pipeline_stage`, and the write portion of the lock tools.

Modes:
    `dry_run=true`  → validate + compile only (local + remote compile,
                      no persistence).
    `dry_run=false` → if `strategy_id` provided: PATCH update; else: POST
                      create.

`description` (optional, ≤2000 chars) is the strategy's THESIS in the
user's own words — what it is for, the market, the edge. It is passed
through to the API's `description` on create and update and shown on the
strategy page, where the user can edit it (Q-1617). It is never the
conversation: the field asks for the thesis, not for what the user said.

Do NOT use to fork an existing strategy — call `keel_strategy_fork`.
Do NOT use to run a backtest — call `keel_backtest_run`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from keel.errors import (
    ConflictError,
    EntitlementError,
    KeelError,
    UsageError,
    ValidationError,
)
from keel.hosting import record_outcome

from . import register
from ._base import (
    STRATEGY_NAME_FACT,
    OutcomeResult,
    OutcomeTool,
    ToolContext,
    listed_schema,
    present_choice,
    present_param_schema,
)


#: This tool's `present` default, as its parameter description states it.
PRESENT_DEFAULT = "`receipt` for a dry run, `view` for a save"


def _retry_args(args: dict, source: str) -> dict[str, Any]:
    """The blocked save, as a call that reruns EXACTLY it (Q-1847).

    Every argument the caller passed that this tool declares — `name`,
    `description`, `message`, `parent_version`, `present`, `strategy_id` —
    with the source as `source` (the text this call READ, so a `source_file`
    call resumes on hosted too, where no filesystem exists). The resume used
    to carry only `strategy_id` and `name`: no source at all, so following
    it after the reset raised `missing_input` — the Q-1807 shape (a resume
    that is not the blocked call) in its most complete form. Filtered by the
    tool's own schema so adapter-internal keys never leak into the call.
    """
    from . import get as get_tool

    declared = get_tool("keel_strategy_compose").input_schema["properties"]
    out = {k: v for k, v in args.items() if k in declared and v is not None}
    out.pop("source_file", None)
    out["source"] = source
    return out


def _maybe_compose_quota_handoff(e: EntitlementError, *, retry_args: dict[str, Any]):
    """Plan-cap wall → shared handoff envelope (spec 03 R1).

    Persisting can hit org plan caps (strategy counts, symbols per
    strategy, feature gates); those 403s carry entitlement reasons and
    only a human billing action clears them. Non-quota 403s return None
    so the caller re-raises the original error untouched.
    """
    from ._handoff import maybe_quota_handoff

    return maybe_quota_handoff(
        e,
        blocked_action="strategy_compose",
        retry_call={"tool": "keel_strategy_compose", "args": retry_args},
    )


def _read_source(args: dict) -> str:
    source = args.get("source")
    # Whitespace is no source (Q-2273): a blank `source` passed `if source`
    # and was sent to keel-api as a strategy. It is treated as absent, so the call is
    # refused with the same `missing_input` an omitted source gets.
    if isinstance(source, str) and not source.strip():
        source = None
    source_file = args.get("source_file")
    if source and source_file:
        raise KeelError(
            "Pass exactly one of `source` or `source_file`, not both.",
            error_code="conflicting_inputs",
            exit_code=2,
            suggestion=(
                "Drop whichever you don't need. `source` is inline DSL text; "
                "`source_file` is a path to a .py file containing the DSL."
            ),
        )
    if source:
        return str(source)
    if source_file:
        from keel.hosting import is_hosted

        if is_hosted():
            # No caller filesystem on the hosted server — the file-path
            # convenience is local-only. Fail instructively instead of
            # reading pod paths.
            raise KeelError(
                "`source_file` is not available on the hosted server — there "
                "is no local filesystem here.",
                error_code="usage_error",
                exit_code=2,
                suggestion=(
                    "Pass the DSL text inline via `source='Globals(...)'` instead of a file path."
                ),
            )
        p = Path(str(source_file))
        if not p.exists():
            raise KeelError(
                f"source_file not found: {source_file}",
                error_code="not_found",
                exit_code=3,
                suggestion=(
                    "Verify the path. Use an absolute path or one relative to "
                    "your cwd. For checked-out strategies, the file is at "
                    "`<workspace>/strategy.py` (find via `keel_strategy_workspaces`)."
                ),
            )
        return p.read_text(encoding="utf-8")
    from ._toolsets import is_listed_profile

    if is_listed_profile():
        # The listed schema has no `source_file` (and drops one a frozen
        # catalog still sends), so the refusal names only what it accepts.
        raise KeelError(
            "Missing required input: pass `source` (the strategy's DSL text).",
            error_code="missing_input",
            exit_code=2,
            suggestion=(
                "Pass the DSL text inline as `source`. `keel_components_search` finds "
                "the components for its Pipeline first."
            ),
        )
    raise KeelError(
        "Missing required input: pass either `source` (DSL text) or `source_file` (path).",
        error_code="missing_input",
        exit_code=2,
        suggestion=(
            "Pass inline DSL via `source='Strategy(...)'`, OR a file path via "
            "`source_file='./strategy.py'`. Use `keel_components_search` to "
            "discover components for the DSL body first."
        ),
    )


def _try_local_validate(
    source: str, *, pre_save: bool = False, base_lock: dict[str, int] | None = None
) -> dict[str, Any]:
    """Use libs/keel local validator. Returns
    {ok: bool, warnings: [], errors: []}.

    ``pre_save=True`` only for the dry run: the save it previews resolves a
    criteria universe, so UNRESOLVED_UNIVERSE is info there, not a warning
    (review 06 §3.2 #4). The save path keeps full severity.

    ``base_lock`` is the strategy's stored lock on an update: its pins are
    kept for every component the source still uses and new components are
    pinned at latest (``evolve_lock`` semantics), so a strategy valid at its
    own pins is not reported invalid because a newer version of one of its
    components changed its interface (dollar-volume spec 02 §3)."""
    try:
        from keel.tools.local import strategy_lock_generate, strategy_validate
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "warnings": [], "errors": [f"validator-unavailable: {e}"]}
    try:
        lock = None
        try:
            lock = strategy_lock_generate(source=source).get("component_lock")
        except Exception:  # noqa: BLE001, S110 — lock generation optional; validation proceeds without it
            pass
        if lock and base_lock:
            lock = {name: base_lock.get(name, version) for name, version in lock.items()}
        result = (
            strategy_validate(source=source, component_lock=lock, pre_save=pre_save)
            if lock
            else strategy_validate(source=source, pre_save=pre_save)
        )
        ok = bool(result.get("valid", False))
        return {
            "ok": ok,
            "warnings": result.get("warnings", []) or [],
            "errors": result.get("errors", []) or [],
            "lock": lock,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "warnings": [], "errors": [str(e)]}


def _error_codes(errors: list[Any]) -> list[str] | None:
    """The rule codes of a validation block's ERRORS, for the audit row (Q-2368).

    Each error the local validator reports is an issue dict carrying its
    catalog ``code`` (``ValidationIssue.to_dict``; a parse failure arrives the
    same way through ``parse_error_issue``). Only the code is taken — never
    the message, the source or an argument — and ``record_outcome`` drops
    anything that is not rule-code shaped. Warnings are left out: they did
    not block the agent. A bare-string error (``validator-unavailable: ...``)
    names no rule and contributes nothing. ``None`` when no error has a code,
    so a valid call records no ``issue_codes`` at all.
    """
    codes = [e["code"] for e in errors if isinstance(e, dict) and isinstance(e.get("code"), str)]
    return codes or None


def _universe_block(source: str, *, as_stored: bool) -> dict[str, Any] | None:
    """What this source will HOLD, read the one way every runtime reads it
    (``pipeline_engine.universe_symbols``, Q-1147/Q-1504).

    ``as_stored=True`` means ``source`` is the server's HEAD after the save
    (the bake has run: every mode carries ``resolved``); ``False`` means the
    caller's input (dry run), where a manual basket already reads as its
    typed symbols and a criteria mode reports that the save resolves it.
    Never raises — a source the parser rejects has no universe to report,
    and the validation block already carries that verdict.
    """
    try:
        from pipeline_engine.dsl.parser import parse_strategy
        from pipeline_engine.universe_symbols import effective_universe_symbols

        u = parse_strategy(source).universe
    except Exception:  # noqa: BLE001 — feedback, never a gate
        return None
    if u is None:
        return None
    symbols = effective_universe_symbols(u)
    if u.resolved is not None:
        how = "resolved"
    elif symbols is not None:
        how = "manual_basket"
    else:
        how = "unresolved"
    block: dict[str, Any] = {
        "mode": u.mode,
        "market": u.market,
        "source": how,
        "as_stored": as_stored,
        "resolved_count": len(symbols) if symbols is not None else None,
        "resolved_preview": list(symbols[:8]) if symbols else [],
    }
    if how == "unresolved":
        block["note"] = (
            "No resolved list yet: the save resolves this universe server-side "
            "(a dry run does not save)."
        )
    elif how == "manual_basket":
        block["note"] = (
            "A manual basket holds its `symbols` (minus exclusions, plus "
            "inclusions) that Keel can trade; the save resolves them into "
            "`resolved` and names any it drops."
        )
    return block


def _names_symbols(source: str) -> bool:
    """Whether the source's Universe names symbols the server must classify
    (a manual basket, or inclusions) and carries no resolved list yet."""
    try:
        from pipeline_engine.dsl.parser import parse_strategy

        u = parse_strategy(source).universe
    except Exception:  # noqa: BLE001 — the validation block carries a parse failure
        return False
    if u is None or u.resolved:
        return False
    return bool((u.mode == "manual" and u.symbols) or u.inclusions)


def _dry_run_universe_block(source: str, ctx: ToolContext) -> dict[str, Any] | None:
    """The dry run's universe block, resolved the way the save will resolve it.

    Q-2283 (spec 03 U2/U5): when the Universe names symbols (a manual basket
    or inclusions), the dry run asks the server's resolver — the one every
    save uses — so a HIP-3 or unknown symbol is reported here, not after a
    save and a refused backtest. A basket with nothing tradeable raises the
    server's `UNIVERSE_NOTHING_TRADEABLE` refusal from this call. A criteria
    universe that names nothing is selected from the venue's own listings, so
    the save's resolution is enough and the block stays the static preview.

    The resolve goes through the CALLER's client (a hosted call carries the
    user's credentials). Any other failure leaves the static preview with
    `resolve_error` saying the preview could not resolve — the save resolves
    authoritatively either way; nothing is silently presented as resolved.
    """
    if not _names_symbols(source):
        return _universe_block(source, as_stored=False)
    from keel.tools.local import universe_resolve

    try:
        resolution = universe_resolve(source, client=ctx.get_client())
    except ValidationError as e:
        if getattr(e, "error_code", None) == "UNIVERSE_NOTHING_TRADEABLE":
            raise
        return _unresolved_preview(source, str(e))
    except KeelError:
        # Auth / entitlement / transport: the tool's normal error envelope,
        # exactly as the compile call above treats them.
        raise
    except Exception as e:  # noqa: BLE001 — preview only; said, never hidden
        return _unresolved_preview(source, f"{type(e).__name__}: {e}")
    block = _universe_block(resolution["source"], as_stored=False)
    if block is None:
        return None
    if resolution.get("resolution_note"):
        block["note"] = resolution["resolution_note"]
        block["dropped"] = list(resolution.get("dropped") or [])
    return block


def _unresolved_preview(source: str, reason: str) -> dict[str, Any] | None:
    block = _universe_block(source, as_stored=False)
    if block is not None:
        block["resolve_error"] = (
            f"The dry run could not preview the resolution ({reason}); the save "
            "resolves this universe server-side."
        )
    return block


def _pipeline_name(source: str) -> str | None:
    """`Pipeline([...], name='sv_roc20')` → `"sv_roc20"`, best-effort.

    A dry run has no stored row to read a name from and the emitter's
    graph carries none, so without this the preview receipt introduced
    the user's strategy as `Untitled` seconds after they named it in the
    source they pasted (Q-1717). Never raises: an unparseable source has
    no name to report and the validation block already says why.
    """
    try:
        from pipeline_engine.dsl.parser import parse_strategy

        name = parse_strategy(source).pipeline.name
    except Exception:  # noqa: BLE001 — a render nicety never fails a dry run
        return None
    return name if isinstance(name, str) and name.strip() else None


def _parse_failure(errors: Any) -> dict[str, Any] | None:
    """The validation block's parse failure, or None when the source parsed.

    Read from the issue the local validator already emitted for it
    (`keel.tools.local.parse_error_issue`) rather than by parsing again, so
    the view and `validation.errors` are the SAME issue and cannot describe
    the failure two ways. `DSLParseError.__str__` always opens with
    "Parse error" (its constructor writes the prefix), whatever parse-tier
    code the parser attached.
    """
    for issue in errors or []:
        if isinstance(issue, dict) and str(issue.get("message") or "").startswith("Parse error"):
            return issue
    return None


def _saved_name(ctx: ToolContext, strategy_id: str | None) -> str | None:
    """The stored strategy's name when a dry run edits one, else None.

    One advisory read. A preview of an edit to "HYPE MACD cash" is about
    that strategy, and titling it by the source's `Pipeline(name=…)`
    identifier made it read as something else. Never raises.
    """
    if not strategy_id:
        return None
    try:
        row = ctx.get_client().get(f"/v1/strategies/{strategy_id}")
    except Exception:  # noqa: BLE001 — a title never fails a dry run
        return None
    name = row.get("name") if isinstance(row, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


#: `name='sv_roc20'` inside the source's `Pipeline(...)` call.
_PIPELINE_NAME_RE = re.compile(r"""\bname\s*=\s*(['"])([^'"\n]{1,200})\1""")


def _declared_pipeline_name(source: str) -> str | None:
    """The `Pipeline(..., name='x')` of a source that does NOT parse.

    `_pipeline_name` asks the parser, which is exactly what a parse failure
    cannot answer — and the R3 card then read "Untitled" beside the name
    the agent had written (Q-1840). A textual read of the LAST `name=` after
    the last `Pipeline(`: the pipeline's own name closes its call, and a
    component parameter called `name` earlier in the list must not win.
    """
    at = source.rfind("Pipeline(")
    if at < 0:
        return None
    found = _PIPELINE_NAME_RE.findall(source[at:])
    return found[-1][1].strip() or None if found else None


def _dry_run_view(
    ctx: ToolContext,
    source: str,
    validation: dict[str, Any],
    hero_url: str | None,
    *,
    size_override: str | None = "receipt",
    strategy_id: str | None = None,
    name: str | None = None,
) -> dict | None:
    """The view for a dry run, from `POST /v1/strategies/parse`.

    The parse endpoint is the server's own `spec_to_graph`, which is the
    derivation every other surface reads — so a dry run shows the user
    the SAME picture the save will produce, not a locally-guessed twin.
    Advisory in every direction: no client, no auth, an unparseable
    source ⇒ no view.

    `hero_url` is None when this dry run has no strategy to open
    (Q-1686). `build_view` then omits the view's `View in Keel:` line,
    so the prose an agent reads carries no link either — the picture is
    still worth showing; the link is not, because there is no target.
    """
    from ._strategy_view import build_view

    try:
        parsed = ctx.get_client().post("/v1/strategies/parse", json={"source": source})
    except Exception:  # noqa: BLE001 — a render nicety never fails a dry run
        return None
    if not isinstance(parsed, dict):
        return None
    # What to call this strategy, in the order the answer is most
    # authoritative: the name the caller asked for, then whatever the
    # server's own parse named it, then the `Pipeline(name=…)` in the
    # source itself (Q-1717).
    # The SAVED strategy's name outranks the source's `Pipeline(name=…)`
    # when the dry run edits one: the pipeline name is an identifier
    # (`hype_long_cash_macd`), the strategy name is what the user sees in
    # the app, and a preview titled by the former read as a different thing.
    resolved_name = (
        name or _saved_name(ctx, strategy_id) or parsed.get("name") or _pipeline_name(source)
    )
    return build_view(
        parsed.get("graph"),
        {"status": "PREVIEW", "validation": validation, "name": resolved_name},
        url=hero_url,
        source=source,
        size_override=size_override,
        object_id=strategy_id,
    )


#: A save closer than this to the one before it is iteration the user
#: is paying for in versions (BUILD §2.7).
RAPID_SAVE_WINDOW_S = 120


def unchanged_line(version: Any) -> str:
    """The fact an unchanged save states (Q-2102), as its catalogue line.

    keel-api owns the fact (`StrategyResponse.unchanged`: the source matched
    HEAD, so no version was written); this is only its wording. A fact, no
    advice — founder: "just minimal here, dont say dont run or anything".
    """
    current = f" (v{version})" if version is not None else ""
    return f"No changes — source matches the current version{current}."


def _head_version_stamp(client: Any, strategy_id: str) -> tuple[Any, Any]:
    """`(sequence_number, created_at)` of the CURRENT head, or (None, None).

    Read BEFORE the PATCH: the HEAD-source read compose already does
    returns `source, sequence_number, commit_id, source_hash` and no
    timestamp, so the rapid-save line has no other source for "when was
    the last save". Advisory in every direction.
    """
    try:
        rows = client.get(f"/v1/strategies/{strategy_id}/versions", limit=1)
    except Exception:  # noqa: BLE001 — a hint never fails a save
        return None, None
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("items") or []
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None, None
    return rows[0].get("sequence_number"), rows[0].get("created_at")


def _rapid_save_next(previous_sequence: Any, previous_created_at: Any, version: Any) -> str | None:
    """`Saved v4 18s after v3 — …` (BUILD §2.7).

    Every save is a version the user sees in their history; a burst of
    them turns one edit into five rows. The line says so at the moment
    it happens, which is the only moment it lands.
    """
    from datetime import UTC, datetime

    if not isinstance(previous_created_at, str) or not previous_created_at:
        return None
    try:
        stamped = datetime.fromisoformat(previous_created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    elapsed = (datetime.now(UTC) - stamped).total_seconds()
    if elapsed < 0 or elapsed >= RAPID_SAVE_WINDOW_S:
        return None
    if version is None or previous_sequence is None:
        return None
    # A fact and a call (spec 02 §2.4 #1(c), R-23) — never how to iterate:
    # "save once per user-visible step" was method, the chat layer's.
    return (
        f"Saved v{version} {int(elapsed)}s after v{previous_sequence} — each save is "
        "a version in the user's history; dry_run=true previews without saving."
    )


def _no_backtest_next(client: Any, strategy_id: Any) -> str | None:
    """`No backtest of this strategy has been run yet.` (BUILD §2.7 / W5 P1).

    A fact in the indicative (spec 05 R-L4 / D-13 L3): the line states that
    no completed run exists and stops. It used to spell out the call —
    `keel_backtest_run(strategy_id=…) runs the platform's default window …`
    — which is a quota-spending suggestion chained onto a save. Whether to
    run one is the user's call, not the result's. Silent the instant a run
    exists.
    """
    from ._backtest_view import normalized_status
    from ._pagination import extract_paginated

    if not strategy_id:
        return None
    try:
        payload = client.get("/v1/backtests", strategy_id=strategy_id, limit=1)
    except Exception:  # noqa: BLE001 — a hint never fails a save
        return None
    rows, _ = extract_paginated(payload)
    if any(normalized_status(row) == "completed" for row in rows if isinstance(row, dict)):
        return None
    return "No backtest of this strategy has been run yet."


def _persisted_change(before: str | None, after: str) -> Any:
    """What this update changed — BOTH halves of the source pair.

    `keel_strategy_diff`'s own `diff_sources` compares the PIPELINE, and
    `_declarations.declarations_between` compares the declarations;
    neither is re-implemented here and neither is optional. Before
    Q-1707 only the pipeline half ran, so an edit to `Globals`,
    `Universe` or `Execution` produced no change block at all (`view.
    change = null`) and a mixed save reported only the component it
    touched — `"1 changed"` beside a silently-moved `bar_offset`.

    The declarations ride under the `declarations` key in the SAME
    `{section: {key: {a, b}}}` shape `keel_backtest_compare` puts on
    its `spec_diff`; `change_from_diff` carries it onto the view.

    Advisory in both halves: no previous source, an unparseable one, or
    a differ failure ⇒ that half is absent and the view degrades — a
    render nicety never fails a save that already succeeded.
    """
    if not before or before == after:
        return None
    try:
        from .strategy_diff import diff_sources

        change = diff_sources(before, after)
    except Exception:  # noqa: BLE001 — a render nicety never fails a save
        return None
    try:
        from ._declarations import declarations_between

        change["declarations"] = declarations_between(before, after)
    except Exception:  # noqa: BLE001, S110 — the pipeline half still stands
        pass
    return change


#: The last resort when neither the caller nor the diff names a change
#: (the before could not be read, or the differ failed). Never empty.
FALLBACK_COMMIT_MESSAGE = "Update strategy source"


def commit_message_for_save(
    given: Any, before: str | None, after: str, *, name: str | None = None
) -> str:
    """The version's message — NEVER empty (Q-1752).

    Every MCP save used to land with an empty `message` in
    strategy.commits, so the version history could not tell a 10% buffer
    variant from a 20% one. Precedence: the caller's `message`; for a
    create, `Create <name>`; else the change itself, worded by the one
    owner of change wording (`commit_message_from_diff`); else a fallback.
    Shared by compose and the workspace push (`keel.workspace.push`).
    """
    if isinstance(given, str) and given.strip():
        return given.strip()
    if before is None:
        return f"Create {name}" if name else FALLBACK_COMMIT_MESSAGE
    from ._strategy_view import commit_message_from_diff

    derived = commit_message_from_diff(_persisted_change(before, after))
    return derived or FALLBACK_COMMIT_MESSAGE


def _stored_lock(client: Any, strategy_id: str) -> tuple[dict[str, int] | None, str | None]:
    """The strategy's stored component lock and keel-api's hash of it.

    Advisory like the HEAD reads below: a failed read sends no lock, which
    keel-api answers by evolving the stored lock itself — the same pins."""
    try:
        strategy = client.get(f"/v1/strategies/{strategy_id}")
    except Exception:  # noqa: BLE001 — advisory read; the save keeps the stored pins either way
        return None, None
    if not isinstance(strategy, dict):
        return None, None
    lock = strategy.get("component_lock")
    lock_hash = strategy.get("lock_hash")
    return (
        dict(lock) if isinstance(lock, dict) and lock else None,
        lock_hash if isinstance(lock_hash, str) and lock_hash else None,
    )


def _requested_lock(args: dict) -> dict[str, int] | None:
    """The caller's ``component_lock`` — ``{component: version}`` — or None.

    The pins the caller composed at (dollar-volume spec 02 §1/§3, DV13): an
    external agent applies an upgrade by moving a pin here and saving. Any
    other shape is a usage error, never a silent fallback to the stored pins.
    """
    raw = args.get("component_lock")
    if raw is None:
        return None
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
        for k, v in raw.items()
    ):
        raise ValidationError(
            "`component_lock` must map component names to integer versions, "
            'e.g. {"RollingUniverseMask": 2}.',
            input={"component_lock": raw},
        )
    return dict(raw)


def _head_source(client: Any, strategy_id: str) -> tuple[str | None, Any]:
    """The stored HEAD source and its sequence number, best-effort."""
    try:
        head = client.get(f"/v1/strategies/{strategy_id}/versions/HEAD/source")
    except Exception:  # noqa: BLE001 — advisory read
        return None, None
    if not isinstance(head, dict):
        return None, None
    source = head.get("source")
    return (
        source if isinstance(source, str) and source else None,
        head.get("sequence_number"),
    )


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    source = _read_source(args)
    strategy_id: str | None = args.get("strategy_id")
    name: str | None = args.get("name")
    dry_run: bool = bool(args.get("dry_run", False)) or ctx.dry_run
    parent_version: str | None = args.get("parent_version")
    description: str | None = args.get("description")
    requested_lock = _requested_lock(args)

    # On the listed (hosted) surface a save of an EXISTING strategy only
    # appends a version (Q-2265 append-only policy, founder 2026-10-01): its
    # name and thesis are set when it is created here and edited in the Keel
    # app, so this tool never replaces either in place and stays
    # destructiveHint=false honestly. Dropped inputs are SAID in the result,
    # never silently ignored. The full/local profile keeps update semantics.
    from ._toolsets import is_listed_profile

    create_only_dropped: list[str] = []
    if strategy_id and is_listed_profile():
        create_only_dropped = [k for k in ("name", "description") if args.get(k) is not None]
        name = None
        description = None

    # The stored pins of the strategy being updated (spec 02 §1): the save
    # sends them back with their hash, so it keeps them (the server evolves
    # them over the edit) unless someone moved them since, which is a 409.
    stored_lock, stored_lock_hash = (
        _stored_lock(ctx.get_client(), strategy_id) if strategy_id else (None, None)
    )
    # A save whose text IS the planner's upgrade of HEAD (the listed
    # profile's route: the POSITION_UPGRADE_AVAILABLE issue's result_source)
    # is valid only at the planner's lock when the upgrade moved a pin
    # (Q-2448): the stored pins re-evolved keep the old one. It is saved as
    # if the caller had passed that lock; any other save is untouched.
    upgrade_lock: dict[str, Any] | None = None
    if strategy_id and requested_lock is None and stored_lock:
        from ._known_issue import head_upgrade_lock

        upgrade_lock = head_upgrade_lock(
            ctx.get_client(), strategy_id, source, base_lock=stored_lock
        )
        if upgrade_lock is not None:
            requested_lock = dict(upgrade_lock["component_lock"])
    # The pins this save is FOR: the caller's lock laid over the stored one
    # (keel-api evolves the same union), else the stored lock.
    base_lock = (
        {**(stored_lock or {}), **requested_lock} if requested_lock is not None else stored_lock
    )

    # 1. Local validation always runs first. A dry run validates as the
    # save it previews (pre_save): the save resolves a criteria universe.
    validation = _try_local_validate(source, pre_save=dry_run, base_lock=base_lock)

    # Per the system policy keel-api ships: validation is FEEDBACK, not a
    # gate. The web app editor (JS validator inline) + keel-api strategy
    # POST/PATCH (logs warnings, doesn't block) + chat-api (validate is a
    # separate read-only tool) all surface validation issues to the user
    # without blocking the save. Only parse / compile errors block.
    # Pre-v0.4.x our MCP wrapper was the outlier — it raised on validation
    # errors. Now we match the rest of the system. See
    # `projects/agent-v2/06-prod-readiness-followups.md` for the deeper
    # rationale (Python vs JS validator divergence on per-component
    # input_type literals — fix is to treat the strict literal check as
    # advisory, matching what runtime + JS already accept).

    if dry_run:
        # Optionally hit the server compile endpoint for richer errors.
        # We still try compile even if local validation flagged issues —
        # compile is the source-of-truth gate.
        compiled: dict[str, Any] | None = None
        compile_error: str | None = None
        try:
            from keel.tools.remote import strategy_compile

            compiled = strategy_compile(source=source, component_lock=validation.get("lock"))
        except (UsageError, ValidationError) as e:
            # 400/422 — the compiler rejected the SOURCE. That is the
            # genuine compile verdict this field exists for.
            compile_error = str(e)
        except KeelError:
            # Auth/entitlement/transport failures are NOT compile verdicts
            # (Q-0499/W3-F F6: a role 403 was reported as compiled:false).
            # Re-raise into the tool's normal error envelope so the agent
            # sees auth_failed/insufficient_entitlements + recovery hints.
            raise
        except Exception as e:  # noqa: BLE001 — local best-effort (e.g. lock plumbing) stays non-fatal
            compile_error = str(e)

        # `/v1/strategies/compile` answers a REFUSAL as a 200 whose body is
        # `{compiled: false, errors: [...]}` (routers/sdk.py) — a non-empty
        # dict, so `bool(compiled)` read every refusal as success and a parse
        # error came back `compiled: true, compile_error: null` beside its
        # own error (Q-1840). The verdict is the field, not the body.
        compiled_ok = isinstance(compiled, dict) and compiled.get("compiled", True) is True
        if isinstance(compiled, dict) and not compiled_ok and compile_error is None:
            refused = [str(e) for e in (compiled.get("errors") or []) if e]
            compile_error = "; ".join(refused) or "The compiler refused this source."
        body: dict[str, Any] = {
            "validation": {
                "ok": validation["ok"] and not validation["errors"],
                "errors": validation["errors"],
                "warnings": validation["warnings"],
            },
            "compiled": compiled_ok,
            "compile_error": compile_error,
            "dry_run": True,
        }
        if upgrade_lock is not None:
            body["lock_changes"] = upgrade_lock["lock_changes"]
        # The audit row's verdict (Q-1840): a dry run that found errors did
        # its job, so it is not `is_error` — but it is not a clean pass.
        record_outcome(
            validation_ok=bool(body["validation"]["ok"] and compiled_ok),
            issue_codes=_error_codes(body["validation"]["errors"]),
        )
        universe = _dry_run_universe_block(source, ctx)
        if universe is not None:
            body["universe"] = universe
        from .open_in_app import app_url_for

        # No strategy ⇒ NO LINK (Q-1686). A card's primary action must go
        # somewhere specific; a dry run with no `strategy_id` has nothing
        # to open, so the envelope says so rather than inventing a target.
        # `{app_url}/strategies` reached the card as the one "Open in Keel"
        # action and landed the user on the strategy LIST, which does not
        # contain the thing they were looking at. Every consumer already
        # handles the absence: `to_envelope` drops `hero_url`/`url_line`
        # (_base.py), the card footer is guarded on `view.url`
        # (card-strategy.js), the adapter's fallback row renders its honest
        # no-link line (host-adapter.js), and the CLI prints no URL line.
        hero_url = app_url_for("strategy", strategy_id, ctx) if strategy_id else None
        # A dry run has no stored strategy to read a graph from, so the
        # graph comes from the server's own parse of this source — the
        # same derivation a save would perform. Advisory: no graph, no
        # view, and the validation block still carries the verdict.
        # A dry run is a STEP: `Preview · 5 blocks · valid`, one line
        # that opens in place (BUILD §2.3, decision #3). The whole
        # structure only when the caller says this is the thing they are
        # about to discuss.
        parse_issue = _parse_failure(body["validation"]["errors"])
        if parse_issue is not None:
            # A source that does not parse has no graph for `/parse` to
            # return (it answers 400), so the view comes from the parse
            # failure itself — the same receipt a validation error gets,
            # never the raw envelope (Q-1840).
            from ._strategy_view import build_parse_error_view

            # No link either: the stored strategy (when `strategy_id` names
            # one) is not this source, and a source that does not parse
            # cannot be saved to become one — "Open strategy in Keel" on an
            # error card opened something the user never made (ChatGPT
            # re-test, 2026-09-23).
            hero_url = None
            view = build_parse_error_view(
                parse_issue,
                name=name or _saved_name(ctx, strategy_id) or _declared_pipeline_name(source),
                source=source,
                object_id=strategy_id,
            )
        else:
            view = _dry_run_view(
                ctx,
                source,
                body["validation"],
                hero_url,
                size_override="structure" if present_choice(args) == "view" else "receipt",
                strategy_id=strategy_id,
                name=name,
            )
        if view is not None:
            body["view"] = view
        return OutcomeResult(
            run_id=strategy_id,
            hero_url=hero_url,
            share_url=None,
            extra=body,
        )

    # 2. Real persist path. Validation issues surface in the response
    # under `validation.errors` / `validation.warnings` but don't block —
    # matches keel-api `_validate_compile_graph` policy (log + proceed).
    # The API runs its own validate + compile: a source that does not
    # compile is REFUSED with 422 `COMPILE_FAILED` and nothing is written
    # (Q-2258) — it reaches the agent as that error envelope, never a save.

    if not strategy_id and not name:
        # POST /v1/strategies requires `name` (min_length=1). Surface a
        # clean error instead of letting keel-api 422.
        raise ValidationError(
            "Creating a new strategy requires `--name`.",
            suggestion="Re-run with --name <slug>, or pass --strategy-id <id> to update an existing strategy.",
            input={"missing": "name"},
        )

    client = ctx.get_client()
    workspace_sync: dict[str, Any] | None = None
    payload: dict[str, Any] = {"source": source}
    if name:
        payload["name"] = name
    if strategy_id:
        # An update keeps the STORED pins, never the bundled registry's
        # latest: honoured by keel-api, that lock would silently move every
        # pin (or roll one back, when this snapshot lags the server). A
        # caller's `component_lock` moves the pins it names — guarded by the
        # same hash of the stored lock it was read against.
        if requested_lock is not None:
            payload["component_lock"] = requested_lock
        elif stored_lock:
            payload["component_lock"] = stored_lock
        if "component_lock" in payload and stored_lock_hash:
            payload["expected_lock_hash"] = stored_lock_hash
    elif requested_lock is not None:
        # A create sends the CALLER's pins only (Q-2270): keel-api now
        # honours `component_lock` on create (refusing a pin the source does
        # not use or that is not registered), so the bundled registry's own
        # lock — which can lag the server — must not ride as if asked for.
        # Unnamed components are pinned at the server's latest.
        payload["component_lock"] = requested_lock
    if parent_version and strategy_id:
        # Update only (Q-1862, spec 03 §2.4): keel-api requires the ref to be
        # HEAD and 409s otherwise. A create has no parent to name.
        payload["parent_version"] = parent_version
    if description is not None:
        # The thesis line, on create AND update (founder Q2, D4 spec §8):
        # a thesis that changes is recorded where the user reads it.
        payload["description"] = description

    previous_source: str | None = None
    previous_sequence: Any = None
    previous_created_at: Any = None
    if strategy_id:
        # What the strategy said BEFORE this save — read first, because
        # after the PATCH it is gone. Feeds the view's change block
        # (PLAN §4.2); a read that fails costs the change, nothing more.
        previous_source, previous_sequence = _head_source(client, strategy_id)
        # When the version being replaced was written — the one fact the
        # HEAD-source read does not carry, and the only source for the
        # rapid-save line. Read BEFORE the PATCH, because afterwards the
        # head IS this save.
        _, previous_created_at = _head_version_stamp(client, strategy_id)
        # Every version carries a message (Q-1752): the caller's, else one
        # derived from the change this save makes against HEAD.
        payload["message"] = commit_message_for_save(args.get("message"), previous_source, source)
        # Update existing
        try:
            result = client.patch(f"/v1/strategies/{strategy_id}", json=payload)
        except EntitlementError as e:
            handoff = _maybe_compose_quota_handoff(e, retry_args=_retry_args(args, source))
            if handoff is not None:
                raise handoff from e
            raise
        except KeelError as e:
            detail = e.detail or {}
            if (
                isinstance(e, ConflictError)
                and payload.get("expected_lock_hash")
                and not detail.get("code")
            ):
                # The stored lock moved since this save read it (another
                # writer upgraded a pin): keel-api's uncoded lock-hash 409.
                # Nothing was written; re-running compose re-reads the pins.
                raise ConflictError(
                    "The strategy's component pins changed since this save read them; "
                    "nothing was saved.",
                    suggestion=(
                        "Re-run keel_strategy_compose: it reads the current pins again. "
                        "Pass `component_lock` again only for the pins you mean to move."
                    ),
                ) from e
            if detail.get("code") == "COMPILE_FAILED":
                # Q-2258: keel-api refused the source for not compiling and
                # wrote nothing — the strategy is still the version it names.
                # The message carries the compiler's words; the saved
                # version's source is the base to fix from.
                head = (
                    detail.get("current_head")
                    if isinstance(detail.get("current_head"), dict)
                    else {}
                )
                seq = head.get("sequence_number")
                e.recovery_tool = "keel_strategy_get"
                e.recovery_tool_args = {"strategy_id": strategy_id, "include_source": True}
                e.suggestion = (
                    "This source does not compile, so nothing was saved"
                    + (f"; the strategy is still v{seq}" if seq is not None else "")
                    + ". Fix the error the message names and save again; "
                    "keel_strategy_get returns the saved source to base the fix on."
                )
            if detail.get("code") == "PARENT_VERSION_NOT_HEAD":
                # Spec 03 §2.4 (Q-1862): the save was based on a version that
                # is no longer HEAD. Nothing was written; the recovery is to
                # read HEAD and re-base the change on it.
                head = (
                    detail.get("current_head")
                    if isinstance(detail.get("current_head"), dict)
                    else {}
                )
                seq = head.get("sequence_number")
                e.recovery_tool = "keel_strategy_get"
                e.recovery_tool_args = {"strategy_id": strategy_id, "include_source": True}
                e.suggestion = (
                    f"`parent_version` {parent_version} is not HEAD"
                    + (f" (HEAD is v{seq})" if seq is not None else "")
                    + "; nothing was saved. keel_strategy_get returns HEAD's source to base the "
                    "change on."
                )
            raise
        except Exception as e:  # noqa: BLE001
            from ._toolsets import local_tools_registered

            # `keel_strategy_push` is local-only: named only where this
            # server registers it (spec 05 §4 item 7).
            raise KeelError(
                f"Failed to update strategy {strategy_id}: {e}",
                suggestion=(
                    (
                        "If the strategy is checked out locally, prefer the "
                        "lightweight-git flow: edit the file, then "
                        "`keel_strategy_push -m '<msg>'`. "
                        if local_tools_registered()
                        else ""
                    )
                    + "`keel_connection_check` reports whether the API itself is healthy."
                ),
            )
        sid = result.get("strategy_id") or strategy_id

        # Pull-through write-back (spec 08 R3): a server-side update to a
        # strategy checked out ON THIS MACHINE propagates into the local
        # working copy + meta in the same operation, so the checkout never
        # silently goes stale here. Hosted servers have no caller
        # filesystem — the branch is a deliberate no-op there (spec 01 R2).
        # Advisory: a workspace problem must never fail the compose (the
        # server update already succeeded).
        from keel.hosting import is_hosted

        if not is_hosted():
            try:
                from keel.workspace import write_back_after_server_update

                workspace_sync = write_back_after_server_update(
                    sid or strategy_id,
                    source=source,
                    server_source_hash=result.get("source_hash"),
                    server_sequence=result.get("current_sequence"),
                    server_name=result.get("name"),
                )
            except Exception:  # noqa: BLE001, S110 — advisory write-back; compose already succeeded
                workspace_sync = None
    else:
        payload["message"] = commit_message_for_save(args.get("message"), None, source, name=name)
        try:
            result = client.post("/v1/strategies", json=payload)
        except EntitlementError as e:
            handoff = _maybe_compose_quota_handoff(e, retry_args=_retry_args(args, source))
            if handoff is not None:
                raise handoff from e
            raise
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to create strategy: {e}",
                suggestion=(
                    "Re-validate the source locally first via "
                    "`keel_strategy_compose dry_run=True`. If validation "
                    "passes but create fails, run `keel_connection_check`."
                ),
            )
        sid = result.get("strategy_id") or result.get("id")

    # The source the save STORED — after the server's bake (keel-api bakes
    # `resolved` into every Universe on create/update, Q-1504). Read once
    # and used for everything that describes the saved version: the
    # validation verdict, the universe block, the change block and the
    # Code tab. Before Q-1747 all four read the SUBMITTED source, so every
    # criteria-universe save reported UNRESOLVED_UNIVERSE ("save the
    # strategy to resolve it" — on the save that just resolved it) and a
    # buffer-only edit showed `Universe · resolved [..] → —`.
    from .strategy_get import read_source

    stored_source = read_source(client, sid) if sid else None
    if stored_source and stored_source != source:
        validation = _try_local_validate(
            stored_source, base_lock=result.get("component_lock") or stored_lock
        )
    saved_source = stored_source or source

    # Surface validation feedback in the response even on successful
    # persist — matches the web app editor pattern where warnings stay
    # visible after save so the agent (and user) can act on them.
    body = {
        "strategy_id": sid,
        "version": result.get("current_sequence") or result.get("version"),
        "validation": {
            "ok": validation["ok"] and not validation["errors"],
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        },
    }
    if requested_lock is not None and isinstance(result.get("component_lock"), dict):
        # What was applied, as keel-api stored it: the caller's pins, and
        # every other component the source uses at its stored version.
        body["component_lock"] = result["component_lock"]
    if upgrade_lock is not None:
        # The pins the recognised upgrade moved (keel-api stored them above).
        body["lock_changes"] = upgrade_lock["lock_changes"]
    if create_only_dropped:
        body["note"] = (
            f"{' and '.join('`' + k + '`' for k in create_only_dropped)} not applied: a "
            "strategy's name and thesis are set when it is created; edit them on its "
            "page in the Keel app (`keel_app_link` returns the link)."
        )
    if workspace_sync is not None:
        body["workspace_sync"] = workspace_sync
    # keel-api's verdict on this save (Q-2102): it matched HEAD, so no new
    # version exists. Read, never re-derived; absent on every other save.
    unchanged = bool(strategy_id) and result.get("unchanged") is True
    if unchanged:
        body["unchanged"] = True
    # The commit this save minted goes to the audit row ONLY (Q-1617): it
    # is the one linkage id the envelope does not carry, and it must not
    # start to — the result the agent reads is unchanged.
    record_outcome(
        commit_id=result.get("head_commit_id"),
        validation_ok=bool(body["validation"]["ok"]),
        issue_codes=_error_codes(body["validation"]["errors"]),
    )
    # What will trade, read from the STORED source (the save bakes
    # `resolved` for every mode — Q-1504), so the agent sees the list the
    # backtest will run before it submits one.
    universe = (
        _universe_block(stored_source, as_stored=True)
        if stored_source
        else _universe_block(source, as_stored=False)
    )
    # What the save's resolution kept and dropped, and why (Q-2283, spec 03
    # U4) — keel-api's own sentence, in the same response as the write.
    resolution = result.get("universe_resolution") if isinstance(result, dict) else None
    if universe is not None and isinstance(resolution, dict) and resolution.get("note"):
        universe["note"] = resolution["note"]
        universe["dropped"] = [
            d.get("symbol") for d in resolution.get("dropped") or [] if isinstance(d, dict)
        ]
    if universe is not None:
        body["universe"] = universe

    from ._strategy_view import resize_view
    from .open_in_app import app_url_for
    from .strategy_get import view_for_strategy

    # Same rule on the persist path (Q-1686): a save the API answered with
    # neither `strategy_id` nor `id` has no target either, and the `view`
    # below is already guarded on `sid` for exactly that reason.
    hero_url = app_url_for("strategy", sid, ctx) if sid else None

    # The moment the user has just made something is the moment an id
    # and a link say nothing (Q-1619): read the stored strategy back and
    # carry the view — with the change, when this save had a before.
    if sid:
        view = view_for_strategy(
            client,
            sid,
            ctx,
            # Stored against stored: the before is HEAD as stored, so the
            # after must be too, or the bake itself reads as an edit.
            diff=_persisted_change(previous_source, saved_source),
            from_version=previous_sequence,
            # Already in hand (read once above): the Code tab shows what the
            # save stored, bake included.
            source=saved_source,
        )
        if view is not None:
            # `present="receipt"` is the caller saying this save is an
            # intermediate step. `present="view"` (and the default) leave
            # the size to the EVENT — `choose_size` already answers
            # "structure" for a create and the change for a save, which is
            # exactly what §2.4 asks for.
            if present_choice(args) == "receipt":
                view = resize_view(view, "receipt")
            body["view"] = view

    # The one conditional `next` line (§2.7). A burst of saves is the
    # more urgent thing to say; "no backtest yet" is the next step.
    # An unchanged save wrote no version, so there is no rapid save to report.
    next_line = (
        None
        if unchanged
        else _rapid_save_next(previous_sequence, previous_created_at, body["version"])
    )
    if next_line is None:
        next_line = _no_backtest_next(client, sid)
    if next_line:
        body["next"] = next_line

    return OutcomeResult(
        run_id=sid,
        hero_url=hero_url,
        share_url=None,
        extra=body,
    )


#: The tool's input schema — the shared one; the listed profile serves
#: `LISTED_INPUT_SCHEMA` (R-8: one helper, `_base.listed_schema`).
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "description": (
                "The strategy as Keel DSL text: declarations plus one Pipeline of named "
                "components and their parameters, as in the example above. Keel validates it "
                "and saves it to the user's Keel account; nothing runs on the user's machine. "
                "Exactly one of `source` or `source_file` required."
            ),
        },
        "source_file": {
            "type": "string",
            "description": "Path to a .py DSL file. Exactly one of `source` or `source_file` required.",
        },
        "strategy_id": {
            "type": "string",
            "description": (
                "If set, the save adds the next version to this strategy; otherwise a new "
                "strategy is created."
            ),
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "description": (
                "The strategy's name: required for a new strategy; on an existing one "
                f"(`strategy_id` set) it sets the name. {STRATEGY_NAME_FACT}"
            ),
        },
        "present": present_param_schema(PRESENT_DEFAULT),
        "dry_run": {
            "type": "boolean",
            "default": False,
            "description": "Only validate + compile, do not persist.",
        },
        "parent_version": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "Optional commit/version ref this update is based on.",
        },
        "component_lock": {
            "type": "object",
            "additionalProperties": {"type": "integer"},
            "description": (
                "Optional component versions to save at, e.g. "
                '{"RollingUniverseMask": 2}; on an update, components it does not name keep '
                "their saved version; on a create, they start at the latest version, and a "
                "pin for a component the source does not use is refused. The result's "
                "`component_lock` is the lock that was saved."
            ),
        },
        "description": {
            "type": "string",
            "maxLength": 2000,
            "description": (
                "The strategy's thesis in the user's own words — one or two "
                "sentences on what this strategy is for, which market, and the "
                "edge it expects (not a transcript of the conversation). "
                "Shown on the strategy page in the Keel app, where the user can "
                "edit it. Pass it on create, and again whenever the thesis changes."
            ),
        },
        "message": {
            "type": "string",
            "maxLength": 200,
            "description": (
                "One line naming what this version changes (e.g. `rebalance "
                "buffer 10% → 20%`) — its label in the version history. "
                "Omitted: derived from the change."
            ),
        },
    },
    "required": [],
}

#: The listed twin: the declared omissions of `_base.LISTED_SCHEMA_OMISSIONS`.
LISTED_INPUT_SCHEMA: dict[str, Any] = listed_schema(
    "keel_strategy_compose",
    INPUT_SCHEMA,
    descriptions={
        # Required on this server (Q-2268): the listed schema omits
        # `source_file`, the only alternative, so `source` is in the listed
        # `required` list (`_base.LISTED_REQUIRED_ADDITIONS`, the one declared
        # exception to R-8's shared `required`).
        "source": (
            "The strategy as Keel DSL text: declarations plus one Pipeline of named "
            "components and their parameters, as in the example above. Keel validates "
            "it and saves it to the user's Keel account; nothing runs on the user's "
            "machine."
        ),
        # Create-only on this surface (Q-2265): a save of an existing strategy
        # appends a version and changes nothing else about it.
        "name": (
            "The new strategy's name, required when creating one (no `strategy_id`). "
            "Used only on create: an existing strategy is renamed in the Keel app. "
            f"{STRATEGY_NAME_FACT}"
        ),
        "description": (
            "The new strategy's thesis in the user's own words — one or two sentences "
            "on what it is for, which market, and the edge it expects (not a transcript "
            "of the conversation). Used only on create; the user edits it on the "
            "strategy page in the Keel app."
        ),
    },
)


STRATEGY_COMPOSE = register(
    OutcomeTool(
        name="keel_strategy_compose",
        required_action="strategy.create",
        cli_path=("strategy", "compose"),
        toolset="backtest",
        # grounded-in: system/chat/tool_usage.md:21-25 (compose only AFTER the
        # two-step discovery); system/reasoning_principles.md:3 +
        # system/tool_usage.md:1-3 ("Pipeline Completeness" — must reach
        # WeightSeries; never persist/backtest incomplete);
        # system/chat/collaboration.md §4/§6 (iterate don't rewrite; never
        # backtest incomplete); context-architecture-design §1.1(2) +
        # strategy-creation skill + system/trading_domain.md:5,22-28 +
        # system/universe_selection.md (DEFAULTS).
        description=(
            "Save a strategy from Keel DSL source — new, or with `strategy_id` its next "
            "version; components come from `keel_components_search` and "
            "`keel_components_get_many`. `dry_run=true` validates and compiles without "
            "saving. Each save of the source adds a new version to the strategy's history "
            "and makes it the latest; nothing is deleted, and its earlier versions and "
            "their backtests stay in its history.\n"
            "\n"
            "Declarations, then one Pipeline:\n"
            "Globals(target_timeframe='1d')\n"
            "Universe(mode='top_volume', top_n=30, market='perp')\n"
            "Execution(rebalance='buffered', buffer_threshold=0.2, buffer_mode='relative', rebalance_method='to_edge')\n"
            "Pipeline([\n"
            "    PriceDataLoader(),\n"
            "    ROC(period=20),\n"
            "    ForecastScaler(avg_abs_target=10.0),\n"
            "    ForecastCapper(limit=20.0),\n"
            "    ForecastWeightNormalizer(target_leverage=1.0),\n"
            "], name='my_strategy')\n"
            "A forecast's sign is its "
            "direction (positive long, negative short); polarity is trend-following by "
            "default, and a mean-reversion signal is the inverted one. A `1d` bar closes "
            "at 00:00 UTC; `bar_offset` moves that close, and with it every result.\n"
            "\n"
            "Unsupported Universe symbols are dropped and named in `universe.note`; "
            "HIP-3 markets (e.g. `xyz:AAPL`) are not supported yet, coming soon.\n"
            "\n"
            "A same-machine local checkout is written back (`workspace_sync`) unless it "
            "has local edits; server HEAD is the source of truth."
        ),
        # Listed-profile copy (agent-surface-cleanup spec 01 §2.5, R-4): the
        # SAME text minus the one surface fact a hosted caller cannot act on
        # — there is no local checkout to write back to. Register is never a
        # reason for an override. The forecast-sizer, FixedWeightSizer and
        # branch-sizing sentences left when their validator rules were armed
        # (G-B; NORMALIZER_BEFORE_CONCAT at WARNING, Q-1869).
        listed_description=(
            "Save a strategy from Keel DSL source — new, or with `strategy_id` its next "
            "version; components come from `keel_components_search` and "
            "`keel_components_get_many`. `dry_run=true` validates and compiles without "
            "saving. Each save of the source adds a new version to the strategy's history "
            "and makes it the latest; nothing is deleted, and its earlier versions and "
            "their backtests stay in its history.\n"
            "\n"
            "Declarations, then one Pipeline:\n"
            "Globals(target_timeframe='1d')\n"
            "Universe(mode='top_volume', top_n=30, market='perp')\n"
            "Execution(rebalance='buffered', buffer_threshold=0.2, buffer_mode='relative', rebalance_method='to_edge')\n"
            "Pipeline([\n"
            "    PriceDataLoader(),\n"
            "    ROC(period=20),\n"
            "    ForecastScaler(avg_abs_target=10.0),\n"
            "    ForecastCapper(limit=20.0),\n"
            "    ForecastWeightNormalizer(target_leverage=1.0),\n"
            "], name='my_strategy')\n"
            "A forecast's sign is its "
            "direction (positive long, negative short); polarity is trend-following by "
            "default, and a mean-reversion signal is the inverted one. A `1d` bar closes "
            "at 00:00 UTC; `bar_offset` moves that close, and with it every result.\n"
            "\n"
            "Unsupported Universe symbols are dropped and named in `universe.note`; "
            "HIP-3 markets (e.g. `xyz:AAPL`) are not supported yet, coming soon."
        ),
        input_schema=INPUT_SCHEMA,
        listed_input_schema=LISTED_INPUT_SCHEMA,
        annotations={
            "title": "Save Strategy",
            "readOnlyHint": False,
            "destructiveHint": False,
            # Without `strategy_id` every call creates a NEW strategy, and
            # every save is a new version (Q-1748) — not idempotent.
            "idempotentHint": False,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
