"""Research refusals keep their code and their own next step (AUDIT batch 5: S2-3, S3 #1, #2, #3; 07 §3.4).

- S2-3: keel-api nests a research 404 (``{"detail": {"code": "RESEARCH_SHARE_NOT_FOUND", "message": …}}``); it fell to
  the generic 404, so its envelope code was ``not_found`` and its text the strategy advice ("list yours via
  ``keel_strategy_search`` … ``keel_strategy_history``"). Now the ``RESEARCH_*`` code is the envelope's own and its
  REFUSALS row says where a readable id comes from.
- S3 #1: a research 429 said "Rate limit, not a plan limit" twice on MCP (the message, then the expected), and the day
  bucket read "try again at 00:00 UTC … retry in 43200 s".
- S3 #2: RESEARCH_PAUSED's ``what_was_expected`` was the schema default "Valid input matching the tool's schema.".
- S3 #3: the CLI's refusal reasons named MCP tools, and ``--format json``'s ``view.markdown`` kept the MCP calls.

SEEDS (each reversed by reversing the edit, results in the commit message).
"""

from __future__ import annotations

import json

import pytest
from keel.errors import rate_limited_error, server_words, translate_http_error
from keel.tools.outcomes import _bootstrap
from keel.tools.outcomes._research_commands import (
    apply_research_refusal,
    cli_refusal_text,
    cli_text,
    refusal_text,
)


_bootstrap()
APP = "https://app.usekeel.io"

#: keel-api's research 404s (routers/research*.py, research_backend._refuse, lifecycle/workspace errors), the args a
#: caller sent, and the next call each row writes (None: words only).
NOT_FOUND = {
    "RESEARCH_SHARE_NOT_FOUND": (
        "There is no shared study at that link.", {"share_id": "S" * 23}, None,
    ),
    "RESEARCH_PROJECT_NOT_FOUND": (
        "There is no project with that id that you can read.", {"project_id": "prj_a"}, "keel_research_brief()",
    ),
    "RESEARCH_STEP_NOT_FOUND": (
        "There is no step named Backtst.", {}, 'keel_help(topic="research_steps")',
    ),
    "RESEARCH_LIBRARY_ITEM_NOT_FOUND": (
        "There is no library item with that name.",
        {"subjects": ['Output("keel/momo@1", output="momo")']},
        'keel_research_get(library="signals")',
    ),
    "RESEARCH_PREVIEW_NOT_FOUND": ("There is no preview with that id to keep.", {}, None),
}  # fmt: skip


def _refused(status: int, detail: dict, args: dict, tool: str = "keel_research_get") -> dict:
    err = translate_http_error(status, json.dumps({"detail": detail}))
    return apply_research_refusal(err, args, app_url=f"{APP}/research", tool=tool).to_envelope()


@pytest.mark.parametrize("code", sorted(NOT_FOUND))
def test_a_research_404_keeps_its_code_and_names_no_strategy_tool(code) -> None:
    words, args, call = NOT_FOUND[code]
    env = _refused(404, {"code": code, "message": words}, args)
    assert env["code"] == code and env["message"] == words, env
    assert env["retryable"] is False and env["exit_code"] == 3
    text = refusal_text(env)
    assert "keel_strategy" not in json.dumps(env) and "keel_strategy" not in text, text
    lines = text.splitlines()
    # D-403: no "error: <code>" line; the code is the envelope's
    assert lines[0] == words and lines[-1] == f"View in Keel: {APP}/research", lines
    assert not any(line.startswith("error:") for line in lines), lines
    if call is not None:
        assert env["suggested_next_action"]["tool"] == call.split("(")[0]
        assert any(line.endswith(call) for line in lines), lines
    # the CLI reads the same refusal with no MCP tool name in it
    assert "keel_" not in cli_refusal_text(env), cli_refusal_text(env)


def test_the_share_404_says_to_ask_for_the_link_again_and_the_step_404_keeps_its_near_misses() -> (
    None
):
    share = _refused(404, {"code": "RESEARCH_SHARE_NOT_FOUND", "message": "There is no shared study at that link."},
                     {"share_id": "S" * 23})  # fmt: skip
    assert "ask whoever shared it for the link again" in refusal_text(share)
    step = _refused(404, {"code": "RESEARCH_STEP_NOT_FOUND", "message": "There is no step named Backtst.",
                          "valid": ["Backtest"]}, {}, tool="keel_research_steps")  # fmt: skip
    assert step["detail"]["valid"] == ["Backtest"]
    assert "valid (1): Backtest" in refusal_text(step).splitlines()
    strategy = _refused(404, {"code": "RESEARCH_LIBRARY_ITEM_NOT_FOUND", "message": "x", "ref": "keel/hold@1"},
                        {"subjects": ['Strategy("keel/hold@1")']}, tool="keel_research_plan")  # fmt: skip
    assert strategy["suggested_next_action"]["args"] == {"library": "strategies"}


def test_control_an_uncoded_and_a_non_research_coded_404_keep_the_generic_advice() -> None:
    plain = translate_http_error(404, json.dumps({"detail": "Strategy not found"}))
    assert plain.error_code == "not_found" and "keel_strategy_search" in (plain.suggestion or "")
    other = translate_http_error(
        404, json.dumps({"detail": {"code": "unknown_variant", "message": "No v9."}})
    )
    assert other.error_code == "not_found" and other.detail == {"code": "unknown_variant"}


def test_the_study_404_carries_its_own_code_too() -> None:
    study = _refused(
        404,
        {"code": "RESEARCH_STUDY_NOT_FOUND", "message": "No such study."},
        {"study_id": "stu_a"},
    )
    assert study["code"] == "RESEARCH_STUDY_NOT_FOUND"
    assert (
        study["detail"]["links"]["app"] == f"{APP}/research"
    )  # a missing study links Research's home


# ── S3 #1: 429 ────────────────────────────────────────────────────────────────


def _rate(words: str, wait: int) -> dict:
    body = json.dumps({"detail": {"code": "rate_limited", "message": words, "retry_after_s": wait}})
    err = rate_limited_error(wait, server_words(body))
    return apply_research_refusal(
        err, {"study_id": "stu_a"}, app_url=f"{APP}/research"
    ).to_envelope()


@pytest.mark.parametrize(
    ("words", "wait", "retry"),
    [
        ("Too many plans this minute; try again in 12 s.", 12, "retry in 12 s"),
        ("Too many plans today; try again at 00:00 UTC.", 43200, "retry at 00:00 UTC"),
    ],
)
def test_a_research_429_says_rate_limit_once_and_its_own_time(words, wait, retry) -> None:
    env = _rate(words, wait)
    text = refusal_text(env)
    assert text.count("not a plan limit") == 1, text
    assert text.splitlines()[0] == f"{words} Rate limit, not a plan limit."
    assert env["what_was_expected"] == f"Rate limit, not a plan limit; {retry}."
    assert "43200 s" not in text and "43200 s" not in env["what_was_expected"]
    assert env["detail"]["retry_after_s"] == wait  # the machine half keeps the seconds


def test_control_a_429_with_no_time_in_its_words_keeps_the_wait_once() -> None:
    err = rate_limited_error(30, None)
    env = apply_research_refusal(err, {}, app_url=f"{APP}/research").to_envelope()
    text = refusal_text(env)
    assert "retry in 30 s" in text and text.count("not a plan limit") == 1, text


# ── S3 #2: paused ─────────────────────────────────────────────────────────────


def test_the_paused_refusal_expects_nothing_of_the_input_and_its_text_stays_one_sentence() -> None:
    err = translate_http_error(
        404, json.dumps({"detail": "Research isn't available yet.", "code": "RESEARCH_PAUSED"})
    )
    env = apply_research_refusal(err, {}, app_url=f"{APP}/research").to_envelope()
    assert env["what_was_expected"] != "Valid input matching the tool's schema."
    assert "Research isn't available on Keel yet" in env["what_was_expected"]
    assert env["suggested_next_action"] == {"tool": None, "args": {}}
    assert refusal_text(env).splitlines() == [
        "Research isn't available yet.",
        f"View in Keel: {APP}/research",
    ]
    assert cli_refusal_text(env).splitlines() == [
        "Research isn't available yet.",
        f"View in Keel: {APP}/research",
    ]


# ── S3 #3: the CLI's words ────────────────────────────────────────────────────


def test_the_cli_refusal_names_keel_commands_never_mcp_tools() -> None:
    env = _refused(
        404,
        {"code": "RESEARCH_STUDY_NOT_FOUND", "message": "No such study."},
        {"study_id": "stu_a"},
    )
    cli = cli_refusal_text(env)
    assert (
        "A study id you can read; this lists your studies: keel research list" in cli.splitlines()
    ), cli
    assert "keel_" not in cli
    # a call inside the served words reads as its command too
    said = 'Plan it first: keel_research_plan(study_id="stu_a", version=2)'
    cli = cli_refusal_text({**env, "message": said})
    assert cli.splitlines()[0] == "Plan it first: keel research plan stu_a --version 2", cli
    # control: the MCP text keeps the MCP call
    assert "keel_research_get()" in refusal_text(env)


def test_cli_json_view_markdown_is_the_cli_text(monkeypatch) -> None:
    """``--format json`` of a research answer: ``view.markdown`` carries the ``keel`` commands (cli_text), the rest of
    the envelope as served. Control: a non-study view's markdown is untouched."""
    import io
    from contextlib import redirect_stdout

    from keel.tools.outcomes._base import OutcomeResult
    from keel.tools.outcomes._cli_adapter import _render

    md = 'Done.\nkeel_research_get(study_id="stu_a", view=0) reads the rows.\nView in Keel: x'
    out = io.StringIO()
    with redirect_stdout(out):
        _render(OutcomeResult(extra={"view": {"kind": "study", "markdown": md}, "n": 1}), "json")
    got = json.loads(out.getvalue())
    assert got["view"]["markdown"] == cli_text(md)
    assert "keel research get stu_a --view 0" in got["view"]["markdown"] and got["n"] == 1
    out = io.StringIO()
    with redirect_stdout(out):
        _render(OutcomeResult(extra={"view": {"kind": "screener", "markdown": md}}), "json")
    assert json.loads(out.getvalue())["view"]["markdown"] == md  # control
