"""Register OutcomeTools as FastMCP tools.

Each outcome's `input_schema` becomes the MCP `inputSchema`; the
handler is wrapped in a FastMCP `@mcp.tool(...)` registration. Errors
are caught and serialised through the spec §13 envelope — the wire
never sees a Python traceback.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from keel.errors import KeelError

from ._base import (
    OutcomeResult,
    OutcomeTool,
    ToolContext,
    envelope_error,
    listed_ignored_arguments,
)
from ._channels import (
    CARD_META_KEY,
    CHANNEL_MAP,
    ERROR_FIELDS,
    card_line_retired,
    card_meta_move,
    is_error_envelope,
    kind_for_tool,
    nonview_text_only,
    partition,
)
from ._strategy_view import VIEW_TOOLS
from ._toolsets import (
    LISTED_PARAM_ALIASES,
    PARAM_ALIASES,
    RETIRED_ARGUMENTS,
    is_listed_profile,
    is_tool_loaded,
    load_toolsets,
)


__all__ = [
    "CARD_SHOWN_LINE",
    "DRAFT_CHECK_LINE",
    "CHANNEL_MAP",
    "ERROR_FIELDS",
    "operational_lines",
    "register_all",
    "view_tool_result",
]


def effective_description(tool: OutcomeTool) -> str:
    """The description published for the active server profile.

    Listed-profile registrations carry policy-vetted copy (spec 01 R3,
    research/08 string rules); everything else uses the shared text.
    """
    if is_listed_profile() and tool.listed_description is not None:
        return tool.listed_description
    return tool.description


def effective_input_schema(tool: OutcomeTool) -> dict:
    """The input schema published for the active server profile.

    Overrides are copy-only (parameter descriptions) EXCEPT for the
    declared omissions in `_base.LISTED_SCHEMA_OMISSIONS` (R-8): every
    property the listed schema keeps has the shared name, type, enum,
    default and required status, so tool behavior never diverges between
    profiles (asserted in tests/test_server_profiles.py).
    """
    if is_listed_profile() and tool.listed_input_schema is not None:
        return tool.listed_input_schema
    return tool.input_schema


def param_aliases(tool: OutcomeTool) -> dict[str, str]:
    """Old argument name → the name it is rewritten to before validation:
    `_toolsets.PARAM_ALIASES` on every profile, plus the listed-only
    `_toolsets.LISTED_PARAM_ALIASES` while the listed schema is served."""
    aliases = dict(PARAM_ALIASES.get(tool.name, {}))
    if is_listed_profile() and tool.listed_input_schema is not None:
        aliases.update(LISTED_PARAM_ALIASES.get(tool.name, {}))
    return aliases


def ignored_arguments(tool: OutcomeTool) -> frozenset[str]:
    """Arguments this profile accepts and drops without a word: the listed
    schema's top-level omissions, only while the listed schema is the one
    served (`_base.listed_ignored_arguments`), plus the tool's RETIRED
    arguments on every profile (`_toolsets.RETIRED_ARGUMENTS`, Q-2267)."""
    retired = RETIRED_ARGUMENTS.get(tool.name, frozenset())
    if is_listed_profile() and tool.listed_input_schema is not None:
        # A listed omission that is ALIASED is rewritten, never dropped.
        rewritten = frozenset(LISTED_PARAM_ALIASES.get(tool.name, {}))
        return (listed_ignored_arguments(tool.name) - rewritten) | retired
    return retired


def effective_annotations(tool: OutcomeTool) -> dict:
    """Annotations for the active server profile (title may be vetted)."""
    if is_listed_profile() and tool.listed_title is not None:
        return {**tool.annotations, "title": tool.listed_title}
    return tool.annotations


#: Platform id prefix → the outcome field it belongs to. `run_id` is the
#: strategy id on the strategy tools and the backtest id on the backtest
#: tools (`_base.OutcomeResult`); the prefix says which without any tool
#: having to be named here.
_RUN_ID_PREFIX_FIELD = {"str_": "strategy_id", "btr_": "backtest_run_id"}


def outcome_ref_from_result(result: OutcomeResult) -> dict[str, Any]:
    """What the result already says about the objects this call touched
    (Q-1617) — the ids and booleans a tool's envelope carries anyway,
    projected onto the closed key set of ``keel.hosting.record_outcome``.

    This is the adapter's half of the split: fields the result already
    holds are recorded HERE, once, for every tool; a tool records only what
    its result does not carry (``keel_strategy_compose`` records the
    ``commit_id`` the API returned, which never enters the envelope).
    Values are handed to ``record_outcome`` unfiltered — its id-shape and
    bool checks are the one gate, so a non-id here is dropped there.
    """
    ref: dict[str, Any] = {}
    run_id = result.run_id
    if isinstance(run_id, str):
        field = _RUN_ID_PREFIX_FIELD.get(run_id[:4])
        if field is not None:
            ref[field] = run_id
    extra = result.extra if isinstance(result.extra, dict) else {}
    strategy_id = extra.get("strategy_id")
    if isinstance(strategy_id, str):
        ref["strategy_id"] = strategy_id
    dry_run = extra.get("dry_run")
    if isinstance(dry_run, bool):
        ref["dry_run"] = dry_run
    return ref


def _wire_error(envelope: dict[str, Any]) -> dict[str, Any]:
    """An error envelope as an MCP call returns it.

    `exit_code` is the CLI's process exit status, kept on the envelope so CI
    scripts that parse the CLI's JSON keep working (`KeelError.to_envelope`).
    Nothing reads it over MCP, and on the LISTED profile it is a CLI fact on
    a surface with no CLI (Q-2268), so the directory connector's errors carry
    `code` and `retryable` without it.
    """
    from ._toolsets import is_listed_profile

    if is_listed_profile():
        envelope = {k: v for k, v in envelope.items() if k != "exit_code"}
    return envelope


def _make_handler(tool: OutcomeTool, toolsets: frozenset[str]):
    """Construct a closure FastMCP can register.

    FastMCP introspects the function signature for runtime argument
    validation; `register_all()` separately publishes the declared
    `input_schema` so tools/list keeps descriptions, enums, formats, and
    required fields. The wrapper:
      1. validates schema args (required + unexpected) and emits the
         spec §13.5 envelope on failure;
      2. assembles a `ToolContext` (MCP is never a TTY),
      3. invokes the handler,
      4. converts return → JSON envelope,
      5. converts errors → spec §13 envelope.

    Unknown args normally fail in FastMCP/Pydantic before this wrapper
    runs because FastMCP does not accept `**kwargs` tool signatures.
    `register_all()` wraps those framework validation errors and
    converts them into the same envelope shape.
    """
    schema = effective_input_schema(tool)
    schema_props: dict = schema.get("properties", {})
    schema_required: set = set(schema.get("required", []))
    known_props: set = set(schema_props.keys())
    # Accepted and dropped (never an `unexpected argument`): a frozen
    # connector catalog still sends the listed schema's omissions and the
    # retired inputs.
    ignored = ignored_arguments(tool)
    # Renamed parameters (`_toolsets.PARAM_ALIASES`): a frozen catalog still
    # sends the old name; it is rewritten to the new one before validation,
    # and the OLD name wins when both arrive (Q-2267). Both always arrive
    # here: `_make_param_synthesized_handler` gives every parameter the
    # schema default, so `skip_readiness=False` is in `kwargs` whether the
    # caller sent it or not, and a `setdefault` on the new name silently
    # discarded every old-name call that reached this handler through
    # FastMCP. A caller that sends the old name sent it on purpose; a
    # caller on the new catalog never sends the old one.
    aliases = param_aliases(tool)

    def handler(**kwargs: Any) -> str:
        # Strip Nones — JSON Schema defaults are handled by the handler
        # itself, not by the adapter.
        args = {k: v for k, v in kwargs.items() if v is not None and k not in ignored}
        for old_name, new_name in aliases.items():
            if old_name in args:
                args[new_name] = args.pop(old_name)

        # ── Pre-flight: validate args via spec §13 envelope ─────────
        # FastMCP's auto-pydantic layer would otherwise raise on
        # missing-required / unexpected-keyword and surface the raw
        # stack trace as opaque text — useless to agents. Validate
        # against the declared input_schema ourselves so the response
        # is always a structured envelope.
        # Every error branch below stamps `is_error` on the request outcome
        # slot (Q-1617) so the audit row can say the call failed without
        # anyone parsing the result envelope. Tools stamp the ids they
        # produce; the adapter owns the error bit because it is the one
        # place every error is serialised.
        from keel.hosting import record_outcome

        provided = set(args.keys())
        missing_required = sorted(schema_required - provided)
        unexpected = sorted(provided - known_props)
        if missing_required or unexpected:
            record_outcome(is_error=True)
            problems: list[str] = []
            if missing_required:
                problems.append(f"missing required argument(s): {', '.join(missing_required)}")
            if unexpected:
                problems.append(
                    f"unexpected argument(s) {', '.join(unexpected)} "
                    f"(known: {', '.join(sorted(known_props)) or '(none)'})"
                )
            return json.dumps(
                envelope_error(
                    code="usage_error",
                    message=f"Invalid arguments to {tool.name}: " + "; ".join(problems) + ".",
                    what_was_expected=(
                        f"An arguments object matching the tool's inputSchema "
                        f"(required={sorted(schema_required)}, "
                        f"known={sorted(known_props)})."
                    ),
                    example={
                        k: schema_props[k].get("description", "") for k in sorted(known_props)
                    },
                    suggested_next_action={
                        "tool": tool.name,
                        "args": {k: None for k in missing_required},
                        "reason": (
                            f"Re-call `{tool.name}` with the named arguments above. "
                            f"Drop any unknown arg names; pass the required ones."
                        ),
                    },
                ),
                default=str,
            )

        import os as _os

        from keel.hosting import current_request_credentials, hosted_auth_error, is_hosted

        # ── Per-request identity (spec 01 R1) ───────────────────────
        # Hosted servers bind the caller's validated Bearer to the
        # request context; the ToolContext gets a KeelClient carrying
        # that token, so every tool call acts as the calling principal.
        # Hosted mode with NO binding fails instructively — a tool must
        # never fall back to pod-ambient credentials.
        request_client = None
        creds = current_request_credentials()
        if creds is not None:
            from keel.client import KeelClient
            from keel.config import KeelConfig

            request_client = KeelClient(
                config=KeelConfig(api_key=creds.token, api_url=creds.api_url)
            )
        elif is_hosted():
            record_outcome(is_error=True)
            return json.dumps(_wire_error(hosted_auth_error().to_envelope()), default=str)

        _app_url = _os.environ.get("KEEL_APP_URL")
        _share_url_root = _os.environ.get("KEEL_SHARE_URL_ROOT")
        ctx = ToolContext(
            api_client=request_client,
            is_tty=False,
            toolsets=toolsets,
            **({"app_url": _app_url} if _app_url else {}),
            **({"share_url_root": _share_url_root} if _share_url_root else {}),
        )
        try:
            result: OutcomeResult = tool.handler(args, ctx)
            record_outcome(**outcome_ref_from_result(result))
            return json.dumps(result.to_envelope(), default=str)
        except KeelError as e:
            # Spec §13.5 envelope comes straight off KeelError. Same
            # shape on the CLI side via output.emit_error so agents
            # parse one structure across both channels.
            record_outcome(is_error=True)
            return json.dumps(_wire_error(e.to_envelope()), default=str)
        except Exception:  # noqa: BLE001
            record_outcome(is_error=True)
            # The exception's own text (a KeyError's repr, a parser's
            # position) is logged for the operator, never the message the
            # model and the user read (Q-2273 L3).
            logging.getLogger(__name__).exception("Unexpected error in %s", tool.name)
            return json.dumps(
                envelope_error(
                    code="internal_error",
                    message=f"Unexpected error in {tool.name}; the call did not complete.",
                    what_was_expected="A successful tool call.",
                    example={},
                    suggested_next_action={
                        "tool": "keel_connection_check",
                        "args": {},
                        # Host-neutral (Q-2268): the tool, never the CLI verb.
                        "reason": "keel_connection_check checks the connection to Keel.",
                    },
                ),
                default=str,
            )
        finally:
            if request_client is not None:
                try:
                    request_client.close()
                except Exception:  # noqa: BLE001, S110 — close is best-effort; response already built
                    pass

    handler.__name__ = tool.name
    handler.__doc__ = effective_description(tool)
    return handler


#: The lines catalogue (agent-surface-cleanup spec 02 §2.4) — ONE owner.
#: `(field, label, class)` in the order the text block lists them; a line's
#: presence is its field's presence (every line is also a `structuredContent`
#: field — R3 — so a host that drops `content` loses no fact). DATA lines
#: appear whenever the datum exists, at every `view.size` (R-6: on claude.ai
#: the text block is the model's ONLY channel); the one ADVICE line (`next`)
#: is conditional and at most one per result, chosen by its producer in
#: R-29's precedence. Voice: a fact, never an adjective, never advice about
#: the reply.
ADVICE = "advice"
DATA = "data"

_OPERATIONAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("next", "next", ADVICE),
    # A save that matched HEAD wrote no version (Q-2102): keel-api's fact.
    ("unchanged", "unchanged", DATA),
    # The window that ran (spec 03 §2.5) — before the config, because it
    # changes what every number in the result covers. From the served
    # `window` object on a completed run; else the submit's window-moved
    # sentence (Q-1842) on a keel-api that serves no object.
    ("window", "window", DATA),
    # How much of that window held positions (spec 07 §5, Q-1993's stamp):
    # the app's one line, so a mostly-warm-up window reads as one.
    ("exposure", "exposure", DATA),
    # Part of the run the caller asked for (`keel_backtest_summarize`
    # start/end/capital, spec 07 §6): keel-api's sentence + what a slice is.
    ("slice", "slice", DATA),
    # What the run ran — its commit's universe, clock, rebalance, blocks
    # (Q-1789) and sizing (spec 03 §2.7).
    ("strategy_config", "config", DATA),
    # The capital, fee rate and slippage the numbers were computed at
    # (Q-2270): `config.init_cash/fees/slippage` were applied and never said.
    ("cost_model", "costs", DATA),
    # A strategy view's Execution as DSL a model can copy (Q-1846).
    ("view.header.execution", "execution", DATA),
    # The evidence on a strategy view is another version's run (ChatGPT R4
    # #5): said as a field the model can key on, only when it is so.
    ("view.evidence.matches_version", "evidence_matches_version", DATA),
    ("reference", "reference", DATA),
    ("realism", "realism", DATA),
    # Replaces the retired nudge (§2.4 #7).
    ("good_result", "good_result", DATA),
    # A library entry's verified facts (Q-1843).
    ("library_facts", "facts", DATA),
    ("quota_notice", "quota", DATA),
    ("error_message", "error", DATA),
    ("info", "info", DATA),
    ("validation", "validation", DATA),
    # The pinned deprecated components of a strategy view (position-layer
    # spec 04-R24): which, the known issue, and the tool that upgrades them
    # on THIS profile. Agent context only (D-29); a backtest view has none.
    ("view.validation.deprecations", "upgrade", DATA),
    # The sample-size fact, formerly filed under `next` (Q-1844).
    ("few_fills_note", "sample", DATA),
    # The DSL `include_source=true` fetched (R4 probe, Q-1874):
    # `view.markdown` draws only the structure and claude.ai hands its
    # model nothing but this text block, so a requested source reached the
    # model on no such host. Last, because it is the long one.
    ("source", "source", DATA),
)

#: Full-profile only (never emitted on listed; counted separately by G4):
#: the deploy-intent link a good result carries on the CLI/local surface.
_FULL_PROFILE_FIELDS: tuple[tuple[str, str, str], ...] = (("deploy", "deploy", DATA),)

#: Issues named on the validation line before it says "+N more". The
#: line is for the model's next move, not a full report — `validation`
#: in `structuredContent` carries every field of every issue.
_MAX_VALIDATION_ISSUES = 5


def _issue_line(issue: Any) -> str | None:
    """One validation issue as the model reads it, with its explain topic.

    `explain_topic` is the `rule:<CODE>` channel (guidance spec L5): the
    one string that turns "MASK_DROPS_DIRECTION" from a label into
    something the agent can look up without guessing a search term.
    A code-less issue (the parse-error and validator-unavailable arms
    return bare strings) still reads — it just has nothing to explain.
    """
    if isinstance(issue, str):
        return issue.strip() or None
    if not isinstance(issue, dict):
        return None
    message = str(issue.get("message") or "").strip()
    code = issue.get("code")
    if not message and not code:
        return None
    # The FIX rides the line too (LANES.md bar #1): once prose leaves the
    # corpus for a `rule:` owner, the validator's suggestion is the only
    # teacher on a host that reads nothing but this text block.
    suggestion = str(issue.get("suggestion") or "").strip()
    fix = f" — fix: {suggestion}" if suggestion else ""
    if isinstance(code, str) and code:
        head = f"{code}: {message}" if message else code
        return f'{head}{fix} (keel_help topic="rule:{code}"){_upgrade_route_suffix(code)}'
    return f"{message}{fix}"


def _upgrade_route_suffix(code: str) -> str:
    """` — keel_strategy_upgrade` on a `POSITION_UPGRADE_AVAILABLE` line (04-R23).

    On a profile that does not serve the upgrade tool (D-48's listed
    fallback) the line names where the upgraded source already is: the
    issue's `suggested_edit.payload.result_source`, in `structuredContent`.
    """
    from ._known_issue import UPGRADE_ISSUE_CODE, UPGRADE_TOOL, upgrade_tool_served

    if code != UPGRADE_ISSUE_CODE:
        return ""
    if upgrade_tool_served():
        return f" — {UPGRADE_TOOL}"
    return " — the issue's suggested_edit carries the upgraded source"


def _issue_rank(issue: Any) -> int:
    """0 for the upgrade issue, 1 for every other warning (04-R23 order)."""
    from ._known_issue import UPGRADE_ISSUE_CODE

    code = issue.get("code") if isinstance(issue, dict) else None
    return 0 if code == UPGRADE_ISSUE_CODE else 1


def _validation_line(validation: Any) -> str | None:
    """`validation: 2 errors, 1 warning — …` or None.

    Warnings ride the line too: a save is never blocked by them, so the
    ONLY place a warning can reach the model on a host that drops
    `structuredContent` is here.
    """
    if not isinstance(validation, dict):
        return None
    errors = [i for i in (validation.get("errors") or []) if i]
    warnings = [i for i in (validation.get("warnings") or []) if i]
    if not errors and not warnings:
        return None
    counts = []
    if errors:
        counts.append(f"{len(errors)} error" + ("" if len(errors) == 1 else "s"))
    if warnings:
        counts.append(f"{len(warnings)} warning" + ("" if len(warnings) == 1 else "s"))
    # Errors, then the position-layer upgrade, then every other warning
    # (04-R23): the upgrade stays inside the first five named issues. A
    # stable sort, so the server's order holds within each group.
    warnings = sorted(warnings, key=_issue_rank)
    rendered = [line for line in (_issue_line(i) for i in errors + warnings) if line]
    shown = rendered[:_MAX_VALIDATION_ISSUES]
    remainder = len(rendered) - len(shown)
    if remainder > 0:
        shown.append(f"+{remainder} more")
    head = f"validation: {', '.join(counts)}"
    return f"{head} — {'; '.join(shown)}" if shown else head


def _execution_line(view: Any) -> str | None:
    """`execution: Execution(rebalance='buffered', buffer_threshold=0.2, …)`.

    For a strategy view only, and for the MODEL (Q-1846): the markdown the
    user reads keeps the card's label ("execution Buffered 0.2"), and this
    line names the same declaration in DSL an agent can copy — the R3 agent
    read "Buffered 0.2" and wrote `Execution(buffer=0.2)`. Derived from the
    view's own header, so no tool has to remember to set it.
    """
    if not isinstance(view, dict) or view.get("kind", "strategy") != "strategy":
        return None
    header = view.get("header")
    execution = header.get("execution") if isinstance(header, dict) else None
    params = execution.get("params") if isinstance(execution, dict) else None
    if not isinstance(params, dict):
        return None
    from ._strategy_view import execution_dsl

    dsl = execution_dsl(params)
    return f"execution: {dsl}" if dsl else None


def _args_text(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    return ", ".join(f"{k}={json.dumps(v, default=str)}" for k, v in args.items())


def resume_next(envelope: dict) -> str | None:
    """Spec 02 §2.4 #1(e) — a plan-wall refusal's resume as the `next` line.

    The executable call is `resume.verify_call.{tool, args}` (`_handoff`,
    review 06 M-5 #5): `{reason}: {tool}({args})`.
    """
    resume = envelope.get("resume")
    call = resume.get("verify_call") if isinstance(resume, dict) else None
    if not isinstance(call, dict) or not call.get("tool"):
        return None
    reason = str(call.get("reason") or "The same call").strip().rstrip(".")
    return f"{reason}: {call['tool']}({_args_text(call.get('args'))})"


def _view_kind(envelope: dict) -> str | None:
    view = envelope.get("view")
    if not isinstance(view, dict):
        return None
    return str(view.get("kind") or "strategy")


def _render_field(field: str, envelope: dict) -> str | None:
    """One catalogue field as its line's TEXT (the label is added by the caller)."""
    from ._backtest_view import (
        good_result_line,
        realism_line,
        reference_line,
        slice_line,
        window_line_for,
    )

    if field == "next":
        value = envelope.get("next")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return resume_next(envelope)
    if field == "unchanged":
        if envelope.get("unchanged") is not True:
            return None
        from .strategy_compose import unchanged_line

        return unchanged_line(envelope.get("version"))
    if field == "window":
        view = envelope.get("view") if isinstance(envelope.get("view"), dict) else {}
        status = envelope.get("status") or view.get("status")
        window = envelope.get("window")
        if isinstance(window, dict) and isinstance(window.get("ran"), dict):
            # The clock the `config:` line names — the run's own commit's
            # declaration (`attach_run_config`), never re-derived here.
            config = view.get("config") if isinstance(view.get("config"), dict) else {}
            return window_line_for(window, status=status, clock=config.get("clock"))
        note = envelope.get("window_note")
        return note.strip() if isinstance(note, str) and note.strip() else None
    if field == "view.header.execution":
        line = _execution_line(envelope.get("view"))
        return line.removeprefix("execution: ") if line else None
    if field == "view.evidence.matches_version":
        view = envelope.get("view") if isinstance(envelope.get("view"), dict) else {}
        evidence = view.get("evidence") if view.get("kind", "strategy") == "strategy" else None
        if not isinstance(evidence, dict) or evidence.get("matches_version") is not False:
            return None
        return (
            f"false — the backtest numbers are v{evidence.get('version')}'s run; "
            f"v{view.get('version')} has no completed run"
        )
    if field == "reference":
        kind = _view_kind(envelope)
        if kind not in ("backtest", "comparison"):
            return None
        many = envelope.get("references")
        if isinstance(many, list) and many:
            # Every hold line a compare was asked for (`holds`, Q-1886).
            lines = [reference_line(r, comparison=kind == "comparison") for r in many]
            return "; ".join(line for line in lines if line) or None
        return reference_line(envelope.get("reference"), comparison=kind == "comparison")
    if field == "realism":
        return realism_line(envelope.get("realism"))
    if field in ("exposure", "cost_model"):
        block = envelope.get(field)
        line = block.get("line") if isinstance(block, dict) else None
        return line if isinstance(line, str) and line else None
    if field == "slice":
        return slice_line(envelope.get("slice"))
    if field == "good_result":
        return good_result_line(envelope.get("good_result"))
    if field == "validation":
        line = _validation_line(envelope.get("validation"))
        return line.removeprefix("validation: ") if line else None
    if field == "view.validation.deprecations":
        view = envelope.get("view") if isinstance(envelope.get("view"), dict) else {}
        if view.get("kind", "strategy") != "strategy":
            return None
        validation = view.get("validation") if isinstance(view.get("validation"), dict) else {}
        from ._known_issue import deprecations_line

        return deprecations_line(validation.get("deprecations"))
    if field == "source":
        from ._strategy_view import requested_source_text

        return requested_source_text(envelope.get("source"), version=envelope.get("version"))
    value = envelope.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def operational_lines(envelope: dict) -> list[str]:
    """The lines appended after `view.markdown` (spec 02 §2.4).

    One owner, so a tool that grows a new result line declares it HERE and
    every host gets it — rather than each tool re-deciding what the model is
    guaranteed to see. `deploy` rides the full profile only.
    """
    fields = _OPERATIONAL_FIELDS
    if not is_listed_profile():
        fields = fields + _FULL_PROFILE_FIELDS
    lines: list[str] = []
    for field, label, _cls in fields:
        text = _render_field(field, envelope)
        if text:
            lines.append(f"{label}: {text}")
    return lines


#: The first line of a card-backed result's text block (Q-1749). claude.ai
#: injects its own "the user can already see the result" note after a widget
#: renders; ChatGPT does not, and a model there restated the pipeline as a
#: code block and pasted the tearsheet URLs the card already carried. It is
#: a DESCRIPTION of what the user sees — never an instruction to a host or
#: its approval classifier (the Q-1748 class) — and it is conditional on the
#: host drawing cards, because a text host shows the markdown instead.
#:
#: Q-1804: the Q-1749 wording ended "restating them in the reply duplicates
#: the card" — advice about the reply, which the claude.ai agent read as a
#: conflict with a text block that carries every metric, and which is the
#: output-handling register ChatGPT's per-call review flags. The line states
#: only what the user is shown. Part C (decisions.md, 2026-09-23) measured
#: no host that renders the card from result-level data, so whether a card
#: is drawn is the descriptor's fact; this line cannot know it and says
#: "where", not "the user sees".
#:
#: R4 (2026-09-23, both hosts, Q-1896): agents still pasted "Backtest ID:
#: btr_…", "Strategy ID: str_…" and full app URLs under a card that already
#: carried them. The line now names what the card carries — the name, the
#: ids and the links — and what the ids and links in this text are for, as
#: facts: a reply can call the thing by its name. Still no instruction to the
#: host and no imperative (the Q-1804 register; test_policy_scan scans it).
CARD_SHOWN_LINE = (
    "card: where the host draws Keel cards, the user sees what follows as a "
    "card carrying its name, links and ids, so a reply can call it by name; "
    "the ids and links here serve tool calls and hosts without cards."
)

#: The same preface for a DRY RUN (Q-1896, the Q-1848 follow-up). A dry run's
#: card is one quiet row — `<name> · Draft check · revising|ready` — while its
#: markdown carries the parser and validator detail, so CARD_SHOWN_LINE's
#: "what follows is shown to the user" was false for it, and an agent that
#: relayed the text relayed parser internals the user never saw. A saved
#: version's card is the user-facing structure.
DRAFT_CHECK_LINE = (
    "card: where the host draws Keel cards, this dry run shows the user one "
    "row (name · Draft check · revising or ready); what follows is detail for "
    "the next edit."
)


def _is_dry_run(envelope: dict) -> bool:
    """The two named dry-run signals the strategy card reads (Q-1848)."""
    if envelope.get("dry_run") is True:
        return True
    view = envelope.get("view")
    return isinstance(view, dict) and str(view.get("status") or "").upper() == "PREVIEW"


def view_tool_result(envelope_json: str, tool_name: str | None = None) -> Any:
    """Split a view-carrying envelope into text + structured content.

    A text host (Claude Code, Codex CLI, Windsurf) gets ONE text block,
    and until now that block was the whole JSON envelope: the model read
    a nested graph and narrated it (00 §2.2). For a tool that carries a
    `view`, the text block becomes `view.markdown` — the same rendering
    the card draws, in the form a terminal can show — and the FULL
    envelope stays machine-reachable as `structuredContent`, which is
    also where the card's host adapter reads it from.

    **The markdown is not the whole text block** (BUILD §2.8). The MCP
    Apps draft says `structuredContent` is "not added to model context"
    while `content` is the "text representation for model context", and
    hosts vary. A backtest receipt is ONE line, so promoting it alone
    would shrink what the model is GUARANTEED to read to that line — and
    `next`, `nudge`, `quota_notice`, `error_message` / `info` and the
    validation issues are exactly the fields it must act on. They follow
    the markdown after a blank line, one per line, in that order.

    `tool_name` adds the result-level `_meta.ui.resourceUri` (BUILD
    §2.6) for a card-backed tool — the same resource the descriptor
    advertises, restated on the result where some hosts look for it.
    A result-level `openai/outputTemplate` is documented nowhere and is
    deliberately NOT emitted (decision D-o; it belongs to the probe).

    **An envelope with no markdown to promote** (an error envelope, a
    tool whose result carried no view) keeps its text block byte for
    byte — the JSON string, which on claude.ai is the ONLY thing the
    model reads (Part C probe (e): claude.ai hides `structuredContent`
    from the model) — and ALSO carries the envelope as
    `structuredContent` (Q-1785). ChatGPT hands a widget its result's
    `structuredContent` as `toolOutput` and nothing else, so an error
    that shipped only as text reached the card as `{}` and drew
    "No result to show here" instead of the error's own words.

    A string that is not a JSON object (unparseable output, a
    framework-level failure) becomes a text-only result: nothing that is
    not an envelope is ever dressed up as one, and a view tool's declared
    output schema (Q-1788) means FastMCP could not wrap a bare string.
    """
    # fastmcp is a hard dependency: an import failure here is a build error
    # to surface, never a reason to return a bare string (Q-2095 — fastmcp 4
    # removed `fastmcp.tools.tool`, and the old fallback turned every result
    # through here into a string FastMCP could not serve).
    from fastmcp.tools import ToolResult
    from mcp.types import TextContent

    try:
        envelope = json.loads(envelope_json)
    except (TypeError, ValueError):
        envelope = None
    if not isinstance(envelope, dict):
        return ToolResult(content=[TextContent(type="text", text=str(envelope_json))])

    card_kind = None
    if tool_name is not None:
        from keel.widgets import card_kind_for_tool

        card_kind = card_kind_for_tool(tool_name)
    channel_kind = kind_for_tool(tool_name) or _view_kind(envelope)

    meta: dict[str, Any] = {}
    if card_kind is not None:
        from keel.widgets import card_resource_uri

        meta["ui"] = {"resourceUri": card_resource_uri(card_kind)}

    if is_error_envelope(envelope):
        # The error envelope, every kind (spec 02 §2.2, R-27): the WHOLE
        # envelope in `structuredContent` (a refusal draws its calm card from
        # it alone — Q-1806), an empty card, and a text block that leads with
        # the human `message`, then the catalogue lines (the resume as `next`,
        # the `error:` and `quota:` data), then the envelope itself — so
        # claude.ai, which reads only this block, loses no field (R4).
        lines = operational_lines(envelope)
        text = str(envelope.get("message") or "").strip()
        if lines:
            text += "\n\n" + "\n".join(lines)
        text += "\n\n" + envelope_json
        if card_meta_move():
            meta[CARD_META_KEY] = {}
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=envelope,
            **({"meta": meta} if meta else {}),
        )

    structured, card = (
        partition(envelope, channel_kind) if channel_kind in CHANNEL_MAP else (envelope, {})
    )
    if card:
        meta[CARD_META_KEY] = card

    view = envelope.get("view")
    markdown = view.get("markdown") if isinstance(view, dict) else None
    if not isinstance(markdown, str) or not markdown.strip():
        # No view to promote: the text block is the envelope JSON as today
        # (claude.ai's only channel) — byte for byte when nothing was
        # dropped, else without the dropped duplicates.
        text = envelope_json if structured == envelope else json.dumps(structured, default=str)
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=structured,
            **({"meta": meta} if meta else {}),
        )

    # No catalogue line ⇒ the text block is the markdown, byte for byte. A
    # card-backed result also carries CARD_SHOWN_LINE until the probe
    # retires it (spec 02 §2.6, R-9).
    text = markdown
    extra_lines = operational_lines(envelope)
    if extra_lines:
        text = markdown.rstrip() + "\n\n" + "\n".join(extra_lines)
    if card_kind is not None and not card_line_retired():
        # A preface, not a trailer: the link line stays where the reader
        # looks for it (test_widgets pins it last).
        preface = DRAFT_CHECK_LINE if _is_dry_run(envelope) else CARD_SHOWN_LINE
        text = preface + "\n\n" + text

    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=structured,
        **({"meta": meta} if meta else {}),
    )


def status_wrapped_result(envelope_json: str) -> Any:
    """`keel_account_status` while the non-view probe arm is OFF (spec 02 §2.7 arm 3).

    An old ChatGPT connector holds the frozen `{"result": string}` output
    schema for this tool (R-25), so `structuredContent` keeps exactly that
    shape — the JSON envelope as one string — while the text block becomes
    the status view's markdown + its lines (§2.5). Every fact is in both
    carriers: the markdown and the JSON string each hold the whole result.
    """
    from fastmcp.tools import ToolResult

    result = view_tool_result(envelope_json, "keel_account_status")
    if not isinstance(result, ToolResult):
        return result
    structured = result.structured_content
    text = json.dumps(structured, default=str) if structured is not None else envelope_json
    return ToolResult(
        content=result.content,
        structured_content={"result": text},
        meta={"fastmcp": {"wrap_result": True}},
    )


#: Non-view tools whose text is reading material (spec 02 §2.2): rendered as
#: markdown under the non-view probe arm. Every other non-view tool keeps its
#: JSON envelope as text ("state and ids stay JSON text").
def _reading_renderers() -> dict[str, Any]:
    from ._reading_markdown import READING_RENDERERS

    return READING_RENDERERS


def text_only_result(envelope_json: str, tool_name: str) -> Any:
    """A non-view tool's result under the probe arm (R5): ONE text block and
    no `structuredContent` — reading material as markdown, state as JSON."""
    from fastmcp.tools import ToolResult
    from mcp.types import TextContent

    text = envelope_json
    renderer = _reading_renderers().get(tool_name)
    if renderer is not None:
        try:
            envelope = json.loads(envelope_json)
        except (TypeError, ValueError):
            envelope = None
        if isinstance(envelope, dict) and not is_error_envelope(envelope):
            rendered = renderer(envelope)
            if isinstance(rendered, str) and rendered.strip():
                text = rendered
    return ToolResult(content=[TextContent(type="text", text=text)])


def live_result(envelope_json: str, tool_name: str) -> Any:
    """`keel_live_monitor` under the card-meta arm (R-21): the text block
    is the PARTITIONED envelope as JSON; the series and render hints move to
    the card. claude.ai's model reads nothing but this text block, so the
    full envelope here put every equity/history point in front of it — the
    render-only data "trim for model, keep for cards" takes out (Q-1894).

    The flag must not change the tool's SHAPE (R-25, review 2): an old
    connector holds the frozen `{"result": string}` output schema this tool
    publishes as a `-> str` tool, so `structuredContent` keeps exactly that
    shape — the partitioned envelope as one JSON string — the way
    `status_wrapped_result` keeps it for `keel_account_status`.
    """
    from fastmcp.tools import ToolResult
    from mcp.types import TextContent

    try:
        envelope = json.loads(envelope_json)
    except (TypeError, ValueError):
        return ToolResult(content=[TextContent(type="text", text=str(envelope_json))])
    if not isinstance(envelope, dict):
        return ToolResult(content=[TextContent(type="text", text=str(envelope_json))])
    structured, card = partition(envelope, "live", move_card=True)
    from keel.widgets import card_kind_for_tool, card_resource_uri

    meta: dict[str, Any] = {}
    card_kind = card_kind_for_tool(tool_name)
    if card_kind is not None:
        meta["ui"] = {"resourceUri": card_resource_uri(card_kind)}
    if card:
        meta[CARD_META_KEY] = card
    meta["fastmcp"] = {"wrap_result": True}
    text = json.dumps(structured, default=str)
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"result": text},
        meta=meta,
    )


def registration_mode(tool_name: str) -> str:
    """How one tool's result crosses MCP — decided ONCE, from the flags.

    * ``view`` — markdown text + partitioned `structuredContent` + declared
      schema (the 12 view tools; `keel_account_status` only under the non-view arm);
    * ``status_wrapped`` — `keel_account_status` while the non-view arm is off;
    * ``live`` — `keel_live_monitor` under the card-meta arm;
    * ``text`` — every other tool under the non-view arm (R5);
    * ``legacy`` — today's `-> str` (FastMCP's `{"result": string}` wrapper).
    """
    if tool_name == "keel_account_status" and not nonview_text_only():
        return "status_wrapped"
    if tool_name in VIEW_TOOLS:
        return "view"
    if kind_for_tool(tool_name) == "live":
        return "live" if card_meta_move() else "legacy"
    if nonview_text_only():
        return "text"
    return "legacy"


def result_for_mode(mode: str, envelope_json: str, tool_name: str) -> Any:
    """The FastMCP result for one envelope, in this tool's registration mode.

    Every mode stamps MCP ``isError`` on an error envelope (Q-2273 B1): a
    Keel tool RETURNS its failures (Q-1731 — raising would render only
    ``str(exc)`` and drop the ``structuredContent`` a refusal card draws
    from), so the wire bit is the only machine signal a host gets that the
    call failed. The envelope itself is unchanged.
    """
    if mode == "view":
        result = view_tool_result(envelope_json, tool_name)
    elif mode == "status_wrapped":
        result = status_wrapped_result(envelope_json)
    elif mode == "live":
        result = live_result(envelope_json, tool_name)
    elif mode == "text":
        result = text_only_result(envelope_json, tool_name)
    else:
        result = envelope_json
    return _flag_error(result, envelope_json)


def _flag_error(result: Any, envelope_json: str) -> Any:
    """``result`` with ``is_error`` set when ``envelope_json`` is an error
    envelope; any other result is returned untouched (byte for byte).

    MCP clients skip output-schema validation on an error result
    (`mcp.client.session.ClientSession.call_tool`), so a view tool's error
    keeps its envelope as `structuredContent` for the card (Q-1785). A
    legacy `-> str` tool's error is built the way FastMCP wraps that
    tool's string (`{"result": <text>}` + the wrap marker), plus the bit.
    """
    try:
        envelope = json.loads(envelope_json)
    except (TypeError, ValueError):
        return result
    if not is_error_envelope(envelope):
        return result
    from fastmcp.tools import ToolResult
    from mcp.types import TextContent

    if isinstance(result, ToolResult):
        return result.model_copy(update={"is_error": True})
    text = str(result)
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"result": text},
        meta={"fastmcp": {"wrap_result": True}},
        is_error=True,
    )


def _make_param_synthesized_handler(tool: OutcomeTool, toolsets: frozenset[str]):
    """Wrap the dispatch handler with parameters generated from JSON schema.

    FastMCP needs a real function signature with type annotations to
    derive the MCP tool's inputSchema. We synthesise one here.

    All synthesized params are OPTIONAL (default None) regardless of
    whether the schema marks them required — we want required-arg
    validation to happen in our spec §13.5 envelope (`_make_handler`)
    rather than as a raw pydantic stacktrace upstream. The schema
    keeps its `required: [...]` list so `tools/list` still tells the
    agent which args are mandatory; `_make_handler` enforces it.

    FastMCP does NOT support `**kwargs` in tool signatures
    (function_parsing.py rejects with `ValueError`), so we synthesize an
    exact signature and handle unknown-argument ValidationError at the
    registered tool object layer.
    """
    schema = effective_input_schema(tool)
    properties: dict = schema.get("properties", {})

    inner = _make_handler(tool, toolsets)
    mode = registration_mode(tool.name)

    # `_make_handler` stays the one envelope serialiser (every direct caller
    # and every test keeps its JSON string); the split into a text block +
    # channels, and the `isError` bit on a failure (Q-2273 B1), happen HERE,
    # at the FastMCP boundary, which is the only layer that can carry them.
    # A legacy tool's SUCCESS is still the bare string FastMCP wraps itself.
    def impl(**kwargs: Any) -> Any:
        return result_for_mode(mode, inner(**kwargs), tool.name)

    # The frozen-catalog arguments this profile drops ride the signature
    # too, so FastMCP's own validation accepts them; the published schema
    # (`_mcp_parameters_schema`) still omits them. A renamed parameter's
    # old name rides the same way, typed as its new name but WITHOUT its
    # default: the old name must reach `_make_handler` only when the caller
    # sent it (Q-2267), where it wins over the new name's synthesized default.
    properties = {
        **properties,
        **{
            name: tool.input_schema.get("properties", {}).get(name, {})
            for name in sorted(ignored_arguments(tool))
            if name not in properties
        },
        **{
            old_name: {k: v for k, v in properties.get(new_name, {}).items() if k != "default"}
            for old_name, new_name in sorted(param_aliases(tool).items())
            if old_name not in properties
        },
    }
    params: list[str] = []
    for prop, prop_schema in properties.items():
        py_type = _json_type_to_py(prop_schema)
        # Always include a default so pydantic never raises
        # `missing_argument` upstream — required-arg enforcement lives
        # in `_make_handler`'s envelope-emitting check.
        default = prop_schema.get("default")
        default_repr = repr(default) if default is not None else "None"
        params.append(f"{prop}: {py_type} = {default_repr}")

    params_str = ", ".join(params)
    args_dict_str = ", ".join(f"'{p}': {p}" for p in properties) if properties else ""

    # A view-carrying tool returns either the JSON string or a
    # ToolResult, so it declares no return type: an inferred `-> str`
    # output schema would tell a strict client to expect
    # `{"result": "<string>"}` and then be handed the envelope object.
    # `status_wrapped` KEEPS `-> str` on purpose: that inferred wrapper is
    # the frozen schema old connectors hold (R-25).
    returns = " -> str" if mode in ("legacy", "status_wrapped", "live") else ""
    func_src = f"def {tool.name}({params_str}){returns}:\n    return _impl(**{{{args_dict_str}}})\n"
    local_ns: dict[str, Any] = {"_impl": impl}
    exec(func_src, local_ns)  # noqa: S102 — controlled code-generation, no user input
    fn = local_ns[tool.name]
    fn.__doc__ = effective_description(tool)
    fn.__module__ = "keel.tools.outcomes._mcp_adapter"
    return fn


def _strip_cli_schema_extensions(value: Any) -> Any:
    """Remove CLI-only schema hints before publishing the MCP schema."""
    if isinstance(value, dict):
        return {
            k: _strip_cli_schema_extensions(v)
            for k, v in value.items()
            if not k.startswith("x-cli-")
        }
    if isinstance(value, list):
        return [_strip_cli_schema_extensions(v) for v in value]
    return value


def _mcp_parameters_schema(tool: OutcomeTool) -> dict:
    """Schema shown in MCP tools/list.

    FastMCP's inferred schema loses important contract details because
    all synthesized parameters are optional by design. Publish Keel's
    declared schema instead so agents can see required fields, enum
    values, formats, descriptions, and top-level strictness before they
    call the tool.
    """
    return _strip_cli_schema_extensions(effective_input_schema(tool))


def _validation_error_envelope(tool: OutcomeTool, raw_error: Exception) -> dict:
    """Convert FastMCP/Pydantic argument validation into the Keel error shape."""
    schema = effective_input_schema(tool)
    props: dict = schema.get("properties", {})
    known = sorted(props)
    details = []
    try:
        # Pydantic ValidationError exposes machine-readable details.
        # fastmcp's re-raised ValidationError has no .errors() but chains
        # the pydantic original via __cause__ — read through the chain.
        source = raw_error if hasattr(raw_error, "errors") else raw_error.__cause__
        errors = source.errors()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 — pydantic detail extraction best-effort → empty details on failure
        errors = []

    unexpected: list[str] = []
    invalid: list[str] = []
    for err in errors:
        loc = err.get("loc") or ()
        field = str(loc[0]) if loc else "argument"
        err_type = str(err.get("type") or "")
        if err_type == "unexpected_keyword_argument":
            unexpected.append(field)
        else:
            invalid.append(f"{field}: {err.get('msg', err_type)}")

    if unexpected:
        details.append(
            "unexpected argument(s) "
            + ", ".join(sorted(unexpected))
            + f" (known: {', '.join(known) or '(none)'})"
        )
    if invalid:
        details.append("invalid argument value(s): " + "; ".join(invalid))
    if not details:
        details.append(str(raw_error))

    return envelope_error(
        code="usage_error",
        message=f"Invalid arguments to {tool.name}: " + "; ".join(details) + ".",
        what_was_expected=(
            f"An arguments object matching the tool's inputSchema "
            f"(required={schema.get('required', [])}, known={known})."
        ),
        example={k: props[k].get("description", "") for k in known},
        suggested_next_action={
            "tool": tool.name,
            "args": {k: None for k in schema.get("required", [])},
            "reason": (
                f"Re-call `{tool.name}` using only the named arguments from "
                "`tools/list`; drop unknown arg names and fill required fields."
            ),
        },
    )


def _wrap_fastmcp_validation_errors(tool_obj: Any, outcome: OutcomeTool) -> None:
    """Patch one FastMCP tool object to return structured validation errors.

    FastMCP validates call arguments against the generated Python
    function signature before invoking our handler. That is correct for
    rejecting unknown fields, but the raw Pydantic error is not useful
    to agents. Wrapping `run` here keeps the exact-signature behavior
    while preserving Keel's structured error contract.
    """
    # fastmcp >= 3.4.1 catches the pydantic ValidationError inside
    # FunctionTool.run and re-raises its OWN fastmcp.exceptions.ValidationError
    # (chained via __cause__), so catching only pydantic's class lets the
    # framework error leak raw to the agent. Catch both, explicitly.
    from fastmcp.exceptions import ValidationError as _FastMCPValidationError
    from pydantic import ValidationError

    _validation_errors: tuple[type[Exception], ...] = (ValidationError, _FastMCPValidationError)

    original_run = tool_obj.run

    async def run_with_keel_validation(arguments: dict[str, Any]):
        try:
            return await original_run(arguments)
        except _validation_errors as e:
            envelope_json = json.dumps(_validation_error_envelope(outcome, e), default=str)
            # The same channel rule as every other result this tool returns:
            # a view tool's error rides as structuredContent (Q-1785) and its
            # declared schema (Q-1788) cannot take a bare string; a text-only
            # tool stays one text block; every mode sets `isError` (Q-2273).
            return result_for_mode(registration_mode(outcome.name), envelope_json, outcome.name)

    # FunctionTool is a Pydantic model and disallows assigning undeclared
    # attributes through normal setattr. `run` is a class method, so use
    # object.__setattr__ for this per-instance adapter.
    object.__setattr__(tool_obj, "run", run_with_keel_validation)


def _json_type_to_py(schema: dict) -> str:
    """Map a JSON Schema primitive type to a Python annotation string."""
    t = schema.get("type")
    if "enum" in schema:
        return "str"
    if isinstance(t, list):
        # A union such as `version` (`integer | string`): pydantic's strict
        # str would refuse the integer an agent naturally sends, so the
        # synthesized signature takes either and the handler normalises.
        members = [_json_type_to_py({"type": m}) for m in t if m != "null"]
        return " | ".join(dict.fromkeys(members)) or "str"
    if t == "boolean":
        return "bool"
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "array":
        return "list"
    if t == "object":
        return "dict"
    return "str"


def register_all(mcp_server: Any, outcomes: dict[str, OutcomeTool]) -> None:
    """Attach every outcome tool to the MCP server, filtered by KEEL_TOOLSETS.

    Tools whose toolset is not in the active set are NOT registered;
    they don't appear in `tools/list` and `tools/call` returns
    "tool not found" if invoked anyway.
    """
    from fastmcp.tools.function_tool import FunctionTool
    from mcp.types import ToolAnnotations

    from keel.widgets import tool_ui_meta

    from ._output_schemas import output_schema_for

    toolsets = load_toolsets()
    for tool in outcomes.values():
        if not is_tool_loaded(tool.toolset, toolsets, local_only=tool.local_only, name=tool.name):
            continue
        fn = _make_param_synthesized_handler(tool, toolsets)
        raw_annotations = effective_annotations(tool)
        annotations = (
            ToolAnnotations(**raw_annotations)
            if isinstance(raw_annotations, dict)
            else raw_annotations
        )
        # A view tool declares its envelope schema (Q-1788, narrowed to its
        # `structuredContent` home by spec 02 §2.8); a text-only result
        # publishes NO schema (R5); a legacy tool — and the live monitor in
        # either flag state (R-25: the card-meta arm must not change its
        # shape) — keeps FastMCP's inferred `{"result": <string>}` wrapper.
        mode = registration_mode(tool.name)
        declared = output_schema_for(tool.name) if mode == "view" else None
        schema_kw: dict[str, Any] = {}
        if declared is not None:
            schema_kw["output_schema"] = declared
        elif mode == "text":
            schema_kw["output_schema"] = None
        tool_obj = FunctionTool.from_function(
            fn,
            name=tool.name,
            description=effective_description(tool),
            annotations=annotations,
            # Card-backed tools advertise their MCP Apps widget +
            # ChatGPT Apps SDK template (spec 06 R2); None for the rest.
            meta=tool_ui_meta(tool.name),
            **schema_kw,
        )
        tool_obj.parameters = _mcp_parameters_schema(tool)
        _wrap_fastmcp_validation_errors(tool_obj, tool)
        mcp_server.add_tool(tool_obj)


def loaded_tool_names(outcomes: dict[str, OutcomeTool]) -> list[str]:
    """Return the names of tools that would be registered under the
    current `KEEL_TOOLSETS`. Used by `keel_account_status`."""
    toolsets = load_toolsets()
    return sorted(
        t.name
        for t in outcomes.values()
        if is_tool_loaded(t.toolset, toolsets, local_only=t.local_only, name=t.name)
    )
