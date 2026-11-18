"""Research's refusal table and route adapters (spec 07 §3.4; the ``_screener_commands.py`` pattern).

keel-api renders every refusal's sentence (``libs/research/words.py``, RL-53);
this table adds only the next step, generated from the registry THIS caller is
served (07 §3.4, AT-14): a tool the surface does not serve becomes
``keel_app_link`` (``_research_view.next_call``'s rule), and a refusal with no
next step says so with an explicit ``None``.

Entitlement refusals keep ``recovery_tool = None``, overriding
``EntitlementError``'s ``keel_auth_login`` default (``errors.py``): the
neutral wall (RL-31) names no plan, price or upgrade, and re-authenticating
fixes no plan limit.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from keel.errors import EntitlementError, KeelError


__all__ = [
    "NO_NEXT",
    "REFUSALS",
    "apply_research_refusal",
    "call",
    "invalid",
    "lead_line",
    "line_before_link",
    "read_base",
    "read_source",
    "refusal_code",
    "refusal_link",
    "cli_call",
    "cli_refusal_text",
    "refusal_text",
    "register_research",
    "usage_refusal",
    "research_url",
    "study_result",
]

Args = Mapping[str, Any]
Detail = Mapping[str, Any]
Builder = Callable[[Args, Detail], dict[str, Any]]

#: Codes whose refusal has no next action by design (07 §3.4's "none").
NO_NEXT = frozenset(
    {
        "RESEARCH_PAUSED",
        "RESEARCH_OVER_BUDGET",
        "RESEARCH_DETAIL_EXPIRED",
        # RI-520: a completed step's detail output its run never stored; nothing an agent can repeat brings it.
        "RESEARCH_DETAIL_NOT_KEPT",
        # RI-034: a stored result file is missing (05 §6's guard); nothing an agent can repeat brings it back.
        "RESEARCH_EVIDENCE_MISSING",
        "RESEARCH_SHARE_REMOVED",
        "RESEARCH_BLEND_NOT_A_STRATEGY",
        "RESEARCH_SIGN_IN_REQUIRED",
        "insufficient_entitlements",
        "quota_cap_reached",
        "quota_exhausted",
        "plan_feature_unavailable",
    }
)


def _study(args: Args, detail: Detail) -> dict[str, Any]:
    return {"study_id": args.get("study_id") or detail.get("study_id")}


def _from_plan(args: Args, detail: Detail) -> dict[str, Any]:
    return {"from_plan": args.get("plan_id") or detail.get("plan_id")}


def _stale_plan(args: Args, detail: Detail) -> dict[str, Any]:
    """RI-381 (04 §1.6, 07 §3.4): a data move refuses with the linked-study offer, and that offer's plan
    (``stale[].offer.plan`` of the ``newer_data`` fact: ``{study_id, source}``) is the way forward. A Draft's Run, its
    plain plan and ``from_plan`` all answer the same stale on moved data, so ``from_plan`` is the next call only when
    the refusal carries no such offer (the study or the engine moved)."""
    for fact in detail.get("stale") or []:
        if isinstance(fact, Mapping) and fact.get("kind") == "newer_data":
            offer = fact.get("offer")
            plan = offer.get("plan") if isinstance(offer, Mapping) else None
            if isinstance(plan, Mapping) and plan:
                return dict(plan)
    return _from_plan(args, detail)


def _held_report(args: Args, detail: Detail) -> dict[str, Any]:
    """RI-461: the held cell report's summary (``<held.step>.summary``, a Backtest's), on the study asked of."""
    held = detail.get("held")
    step = held.get("step") if isinstance(held, Mapping) else None
    out = _study(args, detail)
    if isinstance(step, str) and step:
        out["output"] = f"{step}.summary"
    return out


def _without_cursor(args: Args, detail: Detail) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k != "cursor" and v is not None}


def _conflict_read(args: Args, detail: Detail) -> dict[str, Any]:
    """RI-483 (07 §3.4): a revision conflict's next call reads the study again; on a plan refused because a Draft is
    the head (``detail.draft.version``) it reads THAT Draft, whose read serves the source and the ``base`` to edit
    on. The unversioned read serves the version that ran (07 §4.4), so reading it again would refuse again."""
    out = _study(args, detail)
    draft = detail.get("draft")
    version = draft.get("version") if isinstance(draft, Mapping) else None
    if isinstance(version, int) and not isinstance(version, bool):
        out["version"] = version
    return out


#: 07 §3.4's expected words for a revision conflict: a presentation revision moved (view_set), or (RI-482/RI-483) an
#: edit refused because the study's Draft holds changes it was not made on.
_CONFLICT_WORDS = "The study was edited elsewhere first: read it again and send the change on the current revision."
_DRAFT_AHEAD_WORDS = (
    "The study's Draft holds changes this edit was not made on: read the Draft, make the edit on its source and send"
    " the base that read serves."
)


def _conflict_words(detail: Detail) -> str:
    draft = detail.get("draft")
    return (
        _DRAFT_AHEAD_WORDS
        if isinstance(draft, Mapping) and draft.get("version")
        else _CONFLICT_WORDS
    )


def read_base(env: Mapping[str, Any]) -> dict[str, Any] | None:
    """RI-483 (D-301): the ``base`` a study read serves beside its source, ``{version, revision, evidence_hash}`` of
    the read, copied from ``study`` as keel-api served it; None on a share read or a read that does not carry all
    three. An edit of that read's source sends it with keel_research_plan's edit form."""
    study = env.get("study") if isinstance(env.get("study"), Mapping) else {}
    if study.get("share_id"):
        return None
    version, revision, evidence_hash = (
        study.get("version"),
        study.get("revision"),
        study.get("evidence_hash"),
    )
    if not isinstance(version, int) or not isinstance(revision, int) or revision < 1:
        return None
    if not isinstance(evidence_hash, str) or not evidence_hash:
        return None
    return {"version": version, "revision": revision, "evidence_hash": evidence_hash}


def _view_set(args: Args, detail: Detail) -> dict[str, Any]:
    return {
        "study_id": args.get("study_id") or detail.get("study_id"),
        "expected_revision": detail.get("current_revision", detail.get("revision")),
    }


#: 07 §3.4: code → (the tool that recovers, its arguments, what was expected).
REFUSALS: dict[str, tuple[str | None, Builder | None, str | Callable[[Detail], str]]] = {
    "RESEARCH_PRESENTATION_ONLY": (
        "keel_research_view_set",
        _view_set,
        "The change leaves what was measured as it is: save it as a presentation revision.",
    ),
    # 07 §4.6 / AT-9 (D-170): view_set refuses a changed evidence part with the validator's own code; the fix is
    # a new version, which a plan makes.
    "STUDY_EVIDENCE_CHANGED": (
        "keel_research_plan",
        _study,
        "The change alters what was measured, which a presentation revision cannot: plan it as a new version.",
    ),
    "RESEARCH_PLAN_EXPIRED": (
        "keel_research_plan",
        _from_plan,
        "A plan lasts 24 hours; plan again from the stored record.",
    ),
    "RESEARCH_PLAN_STALE": (
        "keel_research_plan",
        _stale_plan,
        "The study, the engine or the data moved since the plan. When the data moved, plan the newer-data offer the"
        " refusal carries (a linked study on today's data); otherwise plan again from the stored record.",
    ),
    "RESEARCH_REVISION_CONFLICT": (
        "keel_research_get",
        _conflict_read,
        _conflict_words,
    ),
    "RESEARCH_NOT_RUN_YET": (
        "keel_research_get",
        _study,
        "The study's progress names what has run.",
    ),
    "RESEARCH_CURSOR_INVALID": (
        "keel_research_get",
        _without_cursor,
        "A cursor reads on only the query it came from; read again without it.",
    ),
    "RESEARCH_STUDY_NOT_FOUND": (
        "keel_research_get",
        lambda a, d: {},
        "A study id you can read; keel_research_get with no id lists your studies.",
    ),
    "STUDY_SUBJECT_NOT_FOUND": (
        "keel_help",
        lambda a, d: {"topic": "research"},
        "A pinned subject you can read; the research help topic lists Keel's references.",
    ),
    "RESEARCH_NOTHING_TO_SHARE": (
        "keel_research_get",
        _study,
        "A version with a step output to share.",
    ),
    # RI-461 (07 §3.4): the cell form asked for a cell the study already holds; ``held.step`` names its report.
    "RESEARCH_CELL_REPORT_HELD": (
        "keel_research_get",
        _held_report,
        "The study already holds this setting's full report: read its summary instead of planning it again.",
    ),
    "RESEARCH_SHARE_TOO_LARGE": (
        "keel_share_create",
        lambda a, d: {k: v for k, v in a.items() if k != "views"},
        "Fewer views: the refusal names the largest.",
    ),
    # RI-040: a snapshot that lists no coins cannot be copied ("Make it yours") or planned on ("Run on yours");
    # nothing in the caller's input fixes it, so the 422's generic "change that input and retry" would mislead.
    # D-344 (2), CONFORM-MCP F3: the codes the three listed tools can hit that had no next step.
    "RESEARCH_PLAN_NOT_FOUND": (
        "keel_research_plan",
        lambda a, d: {},
        "A plan id you made; plan the study again to get one (a plan lasts 24 hours).",
    ),
    "RESEARCH_VIEW_NOT_FOUND": (
        "keel_research_get",
        _study,
        "A chart the study has: the study read lists its charts by index and title.",
    ),
    "RESEARCH_SUBMISSION_OPEN": (
        "keel_research_get",
        _study,
        "The study is still submitting its run; read it for its progress, then try again once it has started.",
    ),
    "RESEARCH_SHARE_NO_COINS": (
        None,
        None,
        "The share cannot be copied as it stands: ask its author to share it again, or plan the study from a "
        "template on your own subjects.",
    ),
    "RESEARCH_DELETE_SHARED": (
        None,
        None,
        "The study has active shares: revoke them with the delete (keel research delete --revoke-shares).",
    ),
    # RI-504 / RI-516: a costed call (a run, run-draft, resume or retry) sent with no Idempotency-Key header. The
    # SDK's own verbs always send one; a caller of the raw API learns what to send, and that nothing ran.
    "idempotency_key_required": (
        None,
        None,
        "Send the same call again with an Idempotency-Key header (any value unique to this call; a retry with the "
        "same key is never charged twice). Nothing ran and nothing was charged.",
    ),
}


def refusal_code(err: KeelError) -> str:
    detail = err.detail if isinstance(getattr(err, "detail", None), dict) else {}
    code = detail.get("code")
    return code if isinstance(code, str) else err.error_code


#: The near misses one issue's text lists (07 §3.2 rule 4's 25 rows).
_VALID_SHOWN = 25

#: The tools whose refused document is a plan (07 §4.12's quick errors): "Plan refused: …".
PLAN_TOOLS = frozenset({"keel_research_plan"})


#: The issue families whose fix is "an address from the subject's list" (07 §4.12's pass, items 1-4).
_ADDRESS_CODES = ("STUDY_ADDRESS_", "STUDY_AXES_")

#: A pinned subject reference inside a subject call: `"str_…@3"`, `"keel/btc_hold@1"`.
_PINNED_REF = re.compile(r'"([^"\s]+@\d+)"')


def _address_reads(args: Args) -> list[str]:
    """J14 row 1 (09; 07 §4.12): the call that lists every address of each pinned subject the plan named, so an
    agent whose address was refused reads the real ones instead of guessing (``keel_research_get(subject=…)``,
    07 §4.12 "The read"). One per distinct reference, in the order the plan named them."""
    from ._research_view import next_call

    subjects = args.get("subjects") if isinstance(args, Mapping) else None
    refs: list[str] = []
    for subject in subjects if isinstance(subjects, list) else []:
        match = _PINNED_REF.search(str(subject))
        if match and match.group(1) not in refs:
            refs.append(match.group(1))
    return [
        f"The addresses of {ref}: "
        + next_call("keel_research_get", {"subject": f'Strategy("{ref}")'})
        for ref in refs
    ]


def refused_text(detail: Mapping[str, Any], args: Args | None = None) -> str | None:
    """07 §4.12's refusal text for a document the validator refused: the count, one line per error issue
    (``path · message``), and what to do. None when the refusal carries no error issue."""
    issues = detail.get("issues")
    if not isinstance(issues, list):
        return None
    errors = [
        i
        for i in issues
        if isinstance(i, Mapping) and i.get("severity", "error") == "error" and i.get("message")
    ]
    if not errors:
        return None
    n = len(errors)
    lines = [f"Plan refused: {n} issue{'' if n == 1 else 's'}; nothing was planned or spent."]
    for i in errors:
        line = f"{i['path']} · {i['message']}" if i.get("path") else str(i["message"])
        # RI-237: the text no longer carries the envelope (07 §3.2 rule 11), so each issue's served fix and near
        # misses (07 §3.4: `suggestion`, `valid`) are written here, as served.
        if isinstance(i.get("suggestion"), str) and i["suggestion"].strip():
            line += f" {i['suggestion'].strip()}"
        lines.append(line)
        valid = i.get("valid")
        if isinstance(valid, list) and valid:
            shown = ", ".join(str(v) for v in valid[:_VALID_SHOWN])
            lines.append(
                f"valid ({len(valid)}): {shown}{', …' if len(valid) > _VALID_SHOWN else ''}"
            )
    if any(str(i.get("code") or "").startswith(_ADDRESS_CODES) for i in errors):
        lines.extend(_address_reads(args or {}))
    lines.append("Fix each, then plan again.")
    return "\n".join(lines)


def apply_research_refusal(
    err: KeelError, args: Args, *, app_url: str | None = None, tool: str | None = None
) -> KeelError:
    """Set 07 §3.4's next step on a research refusal; other errors pass through.

    The next tool is the generated one for this caller: on the listed surface a
    tool it does not serve becomes ``keel_app_link`` (to ``app_url``). A plan the
    validator refused leads with 07 §4.12's "Plan refused: N issues; nothing was
    planned or spent." and one line per issue.
    """
    code = refusal_code(err)
    detail = err.detail if isinstance(getattr(err, "detail", None), dict) else {}
    if app_url:
        # 07 §3.2 rule 9, §3.4 (RI-237): every research answer, refusals included, links into the app: the
        # study's page when the call names a study, else Research's home (keel-api's ``study_link`` rule for a
        # plan with no study). The MCP text ends with it (:func:`refusal_text`) and the card's
        # structured content carries it.
        err.detail = detail = {**detail, "links": {"app": _study_link(app_url, args)}}
    if tool in PLAN_TOOLS:
        text = refused_text(detail, args)
        if text is not None:
            err.args = (text,)
    if code in NO_NEXT or isinstance(err, EntitlementError):
        err.recovery_tool, err.recovery_tool_args = None, None
        if code == "RESEARCH_PAUSED":
            err.suggestion = None
        return err
    row = REFUSALS.get(code)
    if row is None:
        return err
    tool, build, expected = row
    built = build(args, detail) if build else None
    if tool is not None:
        from ._toolsets import is_listed_profile, listed_profile_tools

        if is_listed_profile() and tool not in listed_profile_tools():
            # keel_app_link takes the study's `id` (RI-238 links a stu_ id to its page); it has no `url`
            # argument, so the `{"url": …}` this once sent was refused as an unexpected argument (RI-237). With
            # no study to name there is no call to make: the text's "View in Keel" line is the way in.
            study_id = args.get("study_id") or detail.get("study_id")
            tool, built = ("keel_app_link", {"id": study_id}) if study_id else (None, None)
    err.recovery_tool = tool
    err.recovery_tool_args = built
    err.suggestion = expected(detail) if callable(expected) else expected
    err.retryable = False
    return err


def refusal_link(envelope: Mapping[str, Any]) -> str | None:
    """The app link a research refusal's envelope carries (``detail.links.app``,
    set by :func:`apply_research_refusal`), or ``None`` for any other error."""
    detail = envelope.get("detail")
    links = detail.get("links") if isinstance(detail, Mapping) else None
    app = links.get("app") if isinstance(links, Mapping) else None
    return app if isinstance(app, str) and app else None


def _study_link(app_url: str, args: Any) -> str:
    """The app link a research answer carries (07 §3.2 rule 9): the study's page when the call names a study, else
    Research's home (``app_url``), keel-api's ``study_link`` rule for a plan with no study."""
    from urllib.parse import urlsplit

    from .open_in_app import research_link

    study_id = args.get("study_id") if isinstance(args, Mapping) else None
    if not (isinstance(study_id, str) and study_id):
        return app_url
    parts = urlsplit(app_url)
    link = research_link("study", study_id, origin=f"{parts.scheme}://{parts.netloc}")
    # an app_url that already is this study's page (a caller that built it) stays as given
    return app_url if app_url.rstrip("/") == link else link


def usage_refusal(
    envelope: Mapping[str, Any], tool: str | None, args: Any, *, app_url: str | None = None
) -> dict[str, Any]:
    """RI-286 (07 §3.2 rules 9 and 11, §3.4): a research tool's argument refusal (the adapter's missing-argument
    pre-flight, FastMCP's argument validation) carries the app link every research answer carries
    (``detail.links.app``: :func:`_study_link` over Research's page for this caller, ``app_url`` or
    ``KEEL_APP_URL`` or the context's default), so its MCP text is :func:`refusal_text`'s (its words, its next call,
    then "View in Keel: {url}", no envelope). Any other tool's envelope is returned unchanged, byte for byte."""
    if not (tool or "").startswith("keel_research_"):
        return dict(envelope)
    import os
    from types import SimpleNamespace

    from ._base import ToolContext

    base = app_url or os.environ.get("KEEL_APP_URL") or ToolContext.app_url
    detail = envelope.get("detail") if isinstance(envelope.get("detail"), Mapping) else {}
    link = _study_link(research_url(SimpleNamespace(app_url=base)), args)
    return {**envelope, "detail": {**detail, "links": {"app": link}}}


def refusal_text(envelope: Mapping[str, Any], lines: Sequence[str] = ()) -> str | None:
    """A research refusal's MCP text block (RI-237; 07 §3.2 rules 9 and 11, §3.4), or ``None`` for an error
    envelope that is not one.

    The refusal's own words (``message``: keel-api's served sentence, or 07 §4.12's "Plan refused: …" lines), the
    adapter's operational ``lines``, the generated next call when the refusal has one (its reason, then the call,
    for THIS caller's surface), and last ``View in Keel: {url}``. The envelope is not appended to the text (rule 11:
    no internals); it stays whole in ``structuredContent``, where the card draws the words (07 §3.3)."""
    from ._research_view import _cap, next_call

    link = refusal_link(envelope)
    if link is None:
        return None
    body = [str(envelope.get("message") or "").strip(), *lines, *recovery_lines(envelope)]
    action = envelope.get("suggested_next_action")
    if isinstance(action, Mapping) and action.get("tool"):
        args = action.get("args") if isinstance(action.get("args"), Mapping) else {}
        call = next_call(str(action["tool"]), args, app_url=link)
        reason = str(action.get("reason") or "").strip()
        body.append(f"{reason} {call}" if reason else call)
    return _cap(body, f"View in Keel: {link}")


#: The what-was-expected every refusal without a research row reads (``errors.py``'s generic default): never
#: repeated in the text, which already says what failed.
_GENERIC_EXPECTED = (
    "change that input and retry",
    "The message says what this call needs.",
    # A plan limit's (errors.quota_suggestion, D-14 rule 5): the served wall already says which limit and when; the
    # instruction rides the envelope after the words.
    "Plan limit;",
)


def recovery_lines(envelope: Mapping[str, Any]) -> list[str]:
    """D-344 (2), CONFORM-MCP F3: what a recovery needs, in words, on the text channel: the refusal's near misses
    (``detail.valid``, as 07 §4.12's refused plan writes them) and, when it names no recovery tool, what was expected
    (a share with no coins: "ask its author to share it again, or …"). A probe of ``RESEARCH_MEASURE_UNKNOWN`` read
    "This output has no measure sharp." and nothing else."""
    out: list[str] = []
    detail = envelope.get("detail") if isinstance(envelope.get("detail"), Mapping) else {}
    valid = detail.get("valid")
    if isinstance(valid, list) and valid:
        shown = ", ".join(str(v) for v in valid[:_VALID_SHOWN])
        out.append(f"valid ({len(valid)}): {shown}{', …' if len(valid) > _VALID_SHOWN else ''}")
    action = envelope.get("suggested_next_action")
    expected = str(envelope.get("what_was_expected") or "").strip()
    no_tool = not (isinstance(action, Mapping) and action.get("tool"))
    if no_tool and expected and not any(g.lower() in expected.lower() for g in _GENERIC_EXPECTED):
        out.append(expected)
    return out


def cli_call(tool: str, args: Mapping[str, Any]) -> str | None:
    """RI-251 (07 §3.4 "the call for this caller's surface"): the generated next call as the ``keel`` command that
    makes it (``keel research plan --study-id stu_… --add '…'``), flags as the CLI adapter registers them; ``None``
    for a tool the CLI has no command for."""
    import json
    import shlex

    from . import OUTCOMES
    from ._cli_adapter import _flag_name, positional_arg_of

    target = OUTCOMES.get(tool)
    if target is None or target.mcp_only or not target.cli_path:
        return None
    words = ["keel", *(p.replace("_", "-") for p in target.cli_path)]
    positional = positional_arg_of(target)
    if positional is not None and isinstance(args.get(positional), str):
        words.append(shlex.quote(args[positional]))
    for key, value in args.items():
        if value is None or value is False or key == positional:
            continue
        flag = _flag_name(key)
        if value is True:
            words.append(flag)
            continue
        for item in value if isinstance(value, list) else [value]:
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            words += [flag, shlex.quote(text)]
    return " ".join(words)


def cli_text(markdown: str) -> str:
    """WL-AG-14 (A3 #5): a research answer's text as the CLI prints it: every generated next call
    (``keel_research_get(study_id="stu_…", view=0)``) becomes the ``keel`` command that makes it (:func:`cli_call`);
    a call the CLI has no command for, or one that does not parse, stays as written. Nothing else changes."""
    import ast
    import re

    out: list[str] = []
    pos = 0
    for found in re.finditer(r"\bkeel_[a-z_]+\(", markdown):
        start = found.start()
        if start < pos:
            continue
        end, depth, quote = found.end(), 1, None
        while end < len(markdown) and depth:
            ch = markdown[end]
            if quote:
                if ch == "\\":
                    end += 1
                elif ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            end += 1
        call_text = markdown[start:end]
        command = None
        try:
            node = ast.parse(call_text, mode="eval").body
            if isinstance(node, ast.Call) and not node.args and isinstance(node.func, ast.Name):
                args = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords if kw.arg}
                command = cli_call(node.func.id, args)
        except (SyntaxError, ValueError):
            command = None
        out.append(markdown[pos:start])
        out.append(command or call_text)
        pos = end
    out.append(markdown[pos:])
    return "".join(out)


def cli_refusal_text(envelope: Mapping[str, Any]) -> str | None:
    """RI-251 (COST-SDK residual 5): a research refusal as the CLI prints it, as :func:`refusal_text` writes the MCP
    text: the served words, the next call (its reason, then the ``keel`` command, :func:`cli_call`; the MCP form
    when the CLI has no command for it), then ``View in Keel: {url}`` last. ``None`` for an error envelope that is
    not one (the CLI's ordinary error printing)."""
    from ._research_view import _cap, next_call

    link = refusal_link(envelope)
    if link is None:
        return None
    body = [str(envelope.get("message") or "").strip(), *recovery_lines(envelope)]
    action = envelope.get("suggested_next_action")
    if isinstance(action, Mapping) and action.get("tool"):
        args = action.get("args") if isinstance(action.get("args"), Mapping) else {}
        call = cli_call(str(action["tool"]), args) or next_call(
            str(action["tool"]), args, app_url=link
        )
        reason = str(action.get("reason") or "").strip()
        body.append(f"{reason} {call}" if reason else call)
    return _cap(body, f"View in Keel: {link}")


# ── Registration and the route call (07 §2.2, §5.1) ──────────────────────────


def invalid(message: str) -> None:
    """A call whose arguments cannot be sent (the CLI's exit 2, ``usage_error``)."""
    from keel.errors import UsageError

    raise UsageError(message)


#: 13 §3.3 (WL-AG-09): every study, share, list and poll read asks keel-api for the card's own data (`card_data`,
#: served only when asked; the app's study page never asks). It rides `_meta["keel/card"]` on MCP (`_channels`).
CARD_FULL: dict[str, str] = {"card": "full"}


def research_url(ctx: Any) -> str:
    """The app's Research page for this caller: ``ctx.app_url`` plus the ``home`` route (07 §5.1,
    ``open_in_app.research_link``)."""
    from .open_in_app import research_link

    return research_link("home", origin=str(getattr(ctx, "app_url", "https://app.usekeel.io")))


def call(
    ctx: Any,
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One keel-api research route. ``None`` values are never sent."""
    client = ctx.get_client()
    clean = {k: v for k, v in (body or params or {}).items() if v is not None}
    if method == "get":
        if headers:
            result = client.get(path, headers=dict(headers), **clean)
        else:
            result = client.get(path, **clean)
    elif method == "post":
        # A POST's body is JSON; its ``params`` ride the query (``?card=full`` on the run routes, WL-SV-21).
        if body is not None:
            clean = {k: v for k, v in body.items() if v is not None}
        query = (
            {k: v for k, v in (params or {}).items() if v is not None} if body is not None else {}
        )
        if headers:
            result = client.post(path, json=clean, headers=dict(headers), **query)
        else:
            result = client.post(path, json=clean, **query)
    elif method == "put":
        result = client.put(path, json=clean)
    elif method == "patch":
        result = client.patch(path, json=clean)
    elif method == "delete" and body is not None:
        # A route that takes its DELETE as a body (the project items, RI-178): KeelClient.delete sends params only,
        # so the body goes through the client's one request door (path checks, auth, retries) as JSON.
        result = client._request("DELETE", path, json=clean)
    elif method == "delete":
        result = client.delete(path, **clean)
    else:  # pragma: no cover - a programming error
        raise ValueError(method)
    return result if isinstance(result, dict) else {"items": result}


def read_source(args: Mapping[str, Any]) -> str | None:
    """``source``, or the CLI's ``--source-file`` twin (07 §2.2's CLI forms)."""
    from pathlib import Path

    if args.get("source") is not None and args.get("source_file") is not None:
        invalid("Send source or source_file, not both.")
    if args.get("source_file") is not None:
        return Path(str(args["source_file"])).expanduser().read_text()
    return args.get("source")


#: 13 §2.1 (WL-AG-10): the served discriminator the card picks its layout by, never a sniffed shape. Only a read of
#: one whole study (`study`) draws the study card; a plan is one row, a run the fixed-height run card, the list a list
#: card, one chart's data a chart card, and addresses and a project one receipt row (RI-603: these four drew an
#: empty "Untitled" study card).
VIEW_MODES = frozenset({"plan", "run", "study", "list", "chart", "addresses", "project", "library"})
#: 13 §2.1's sizes: `progress` is the run card's one fixed height.
VIEW_SIZES = frozenset({"receipt", "view", "progress"})


def study_result(
    env: Mapping[str, Any], text: str, *, size: str = "view", mode: str = "study"
) -> Any:
    """The envelope every research tool returns: the served JSON, and the text
    as ``view.markdown`` (``kind: "study"``, 07 §3.1), with ``view.mode`` and
    ``view.size`` set here for every tool and read (13 §2.1)."""
    if mode not in VIEW_MODES or size not in VIEW_SIZES:
        raise ValueError(f"research view mode/size {mode!r}/{size!r} is not one of 13 §2.1's")
    from ._base import OutcomeResult, election_key

    extra = {k: v for k, v in env.items() if k != "view"}
    study = env.get("study") if isinstance(env.get("study"), Mapping) else {}
    obj = study.get("study_id") or study.get("share_id")
    # `{object, at, seq}`: which card a newer one supersedes (the shared rule).
    extra["view"] = {
        "kind": "study",
        "mode": mode,
        "size": size,
        "markdown": text,
        **election_key(obj),
    }
    if isinstance(study.get("version"), int):
        # CARDUI (13 §4.4): the version a card reads joins its election key, so an older version's card reads
        # "shown below" rather than "superseded" when a newer card of the same study arrives.
        extra["view"]["version"] = study["version"]
    hero = (env.get("links") or {}).get("app") if isinstance(env.get("links"), Mapping) else None
    return OutcomeResult(hero_url=hero, extra=extra)


def _guarded(handler: Callable[..., Any], tool: str | None = None) -> Callable[..., Any]:
    def guarded(args: dict, ctx: Any) -> Any:
        try:
            return handler(args, ctx)
        except KeelError as err:
            apply_research_refusal(err, args, app_url=research_url(ctx), tool=tool)
            raise

    return guarded


def register_research(
    name: str,
    path: tuple[str, ...],
    description: str,
    properties: dict[str, Any],
    required: list[str],
    action: str,
    handler: Callable[..., Any],
    *,
    read: bool,
    title: str,
    rate_tier: str | None = None,
    cli_only: bool = False,
    listed_description: str | None = None,
    listed_omit: tuple[str, ...] = (),
    destructive: bool = False,
    idempotent: bool = False,
    toolset: str | None = None,
) -> Any:
    """Register one research OutcomeTool (07 §5.1).

    ``read`` tools resolve to the ``read`` rate tier; every other hostable
    tool states its tier (Q-6789). ``listed_omit`` names the arguments the
    listed schema leaves out (the local-file twin).
    """
    from . import register
    from ._base import LISTED_SCHEMA_OMISSIONS, OutcomeTool, listed_schema

    full_name = f"keel_{name}"
    schema = {"type": "object", "properties": properties, "required": required}
    listed = None
    if listed_description is not None:
        if listed_omit:
            LISTED_SCHEMA_OMISSIONS[full_name] = frozenset(listed_omit)
        listed = listed_schema(full_name, schema)
    return register(
        OutcomeTool(
            name=full_name,
            cli_path=path,
            toolset=toolset or ("read-only" if read else "backtest"),
            description=description,
            listed_description=listed_description,
            input_schema=schema,
            listed_input_schema=listed,
            annotations={
                "title": title,
                "readOnlyHint": read,
                "destructiveHint": destructive,
                # A read is idempotent; a write states it.
                "idempotentHint": idempotent or read,
                "openWorldHint": False,
            },
            handler=_guarded(handler, full_name),
            required_action=action,
            rate_tier=rate_tier,
            cli_only=cli_only,
            confirm_in_cli=destructive,
        )
    )


def line_before_link(text: str, line: str | None) -> str:
    """Add ``line`` above the text's last line ("View in Keel: …", 07 §3.2 rule 9)."""
    if not line:
        return text
    head, sep, last = text.rpartition("\n")
    return f"{head}\n{line}\n{last}" if sep else f"{line}\n{text}"


def lead_line(text: str, line: str | None) -> str:
    """Put ``line`` first (an edit answers with what it now shows, the Q-3639 lesson)."""
    return f"{line}\n{text}" if line else text
