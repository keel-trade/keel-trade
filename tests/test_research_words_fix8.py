"""FIX-8 (AUDIT-WORDS, PRODUCT-READY-RUBRIC §1): the MCP and CLI research text in the reader's words.

Each arm holds one class the audit found and names its seed (applied, seen red, reversed) in its docstring.
"""

from __future__ import annotations

import re

from keel.tools.outcomes import _research_view as rv
from keel.tools.outcomes import research_cli, research_plan, research_run  # noqa: F401  registers the CLI verbs
from keel.tools.outcomes._research_commands import cli_text

#: A snake_case key or a raw state token in a line a person reads (the rubric's mechanical class).
SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")


def _check_env(*, bars: bool) -> dict:
    """A Check read whose eight rows are measured; with ``bars`` False no bar is set (a seed's Checks)."""
    rows = []
    for i in range(8):
        row = {
            "label": f"Check {i}",
            "ref": f"s{i}.summary",
            "measure": "sharpe",
            "bar_index": i,
            "value": 0.5 + i / 10,
            "unit": "ratio",
            "value_words": f"{0.5 + i / 10:.2f}",
            "interval_words": None,
            "withheld_reason": None,
            "sample_words": "Aug 15, 2024 to Sep 29, 2026",
            "met": (i % 2 == 0) if bars else None,
            "op": ">=" if bars else None,
            "bar": 0.3 if bars else None,
            # a snapshot frozen before D-407 still says "No bar set"; the text never prints it
            "bar_words": "your bar ≥ 0.30" if bars else "No bar set",
            "locked": False,
        }
        rows.append(row)
    return {
        "study": {
            "study_id": "stu_abc",
            "title": "Momentum",
            "version": 8,
            "state": "completed",
            "kind_label": {"name": "Check a strategy", "key": "check"},
        },
        "card_summary": {"state_words": "Done · Nov 21"},
        "checks": {"count": 8 if bars else 0, "met": 4 if bars else 0, "rows": rows},
        "links": {"app": "https://app.usekeel.io/research/stu_abc"},
    }


def test_m5_a_check_with_no_bars_says_its_numbers_and_how_to_judge_them() -> None:
    """M5 (S1): a seed's Check (no bar set) read "Your bars (0; met: 0):" over eight measured rows that each ended
    "No bar set; not measured". It says the numbers, that no bar is set, and the call that sets one.
    Seed (reversed): ``count == 0`` branch removed in ``_bars_lines`` reds every arm. Control: a Check with bars keeps
    its verdicts and its bar words."""
    text = rv.study_text(_check_env(bars=False))
    lines = text.splitlines()
    rows = [line for line in lines if line.startswith("- Check ")]
    assert len(rows) == 8, text  # non-vacuity: every measured row is written
    assert "Your checks (8; no bars set yet):" in lines, text
    assert "not measured" not in text and "No bar set" not in text, text
    assert rows[0] == "- Check 0: 0.50, no interval; Aug 15, 2024 to Sep 29, 2026", rows[0]
    # D-407 (2): the app's one line, in its words ("Set a bar on any check to see whether it meets it")
    assert any(
        line.startswith(
            "Set a bar on any check to see whether it meets it: keel_research_view_set("
        )
        for line in lines
    ), text
    assert "Make it yours" not in text, text
    with_bars = rv.study_text(_check_env(bars=True))
    assert "Your checks (8; 4 met):" in with_bars  # control
    assert with_bars.count("; met") == 4 and with_bars.count("; not met") == 4
    assert "Set a bar" not in with_bars


def test_m1_m22_m12_states_makers_and_note_kinds_read_in_words() -> None:
    """M1: a planned study read "· planned" (the raw state) when the card served no state words; M22: a share made by
    the reader's agent read "shared by your_agent"; M12: a note's kind read "set_aside". Seed (reversed):
    ``_state_words`` returning ``study.state`` reds the first arm. Control: served state words win."""
    env = _check_env(bars=True)
    env["card_summary"] = {}
    env["study"]["state"] = "planned"
    assert rv.study_text(env).splitlines()[0].endswith("· version 8 · Planned")
    env["card_summary"] = {"state_words": "Running · 9 of 26 runs"}
    assert rv.study_text(env).splitlines()[0].endswith("· Running · 9 of 26 runs")  # control
    share = {
        "study": {"share_id": "shr_x", "title": "Momentum", "kind_label": {"name": "Check a strategy"}},
        "card_summary": {"maker": "your_agent", "maker_words": "by your agent"},
    }
    assert rv._first_line(share).endswith("· shared by your agent")
    assert rv._first_line({**share, "card_summary": {"maker": "your_agent"}}).endswith(
        "· shared by your agent"
    )
    assert [rv.note_kind_words(k) for k in ("set_aside", "agent_session", "decision")] == [
        "Set aside",
        "Agent session",
        "Decision",
    ]
    assert rv.note_kind_words("new_kind") == "New kind"  # a kind added later still reads as words


def _table_page() -> dict:
    return {
        "data": [
            {
                "result": "Momentum",
                "fast": 10,
                "slow": 40,
                "status": "computed",
                "cell": "fast 10 · slow 40",
                "b": 1000,
                "grain": "month",
                "sharpe": 1.17,
                "sharpe_lo": -0.21,
                "sharpe_hi": 2.41,
                "sharpe_withheld": None,
                "ic_before": 0.03,
                "days_before": 120,
                "wiped_out_on": None,
                "words": {"sharpe": "1.17", "sharpe_lo": "−0.21", "sharpe_hi": "2.41"},
            },
            {
                "result": "Momentum",
                "fast": 10,
                "slow": 50,
                "status": "not_run",
                "cell": "fast 10 · slow 50",
                "b": 1000,
                "grain": "month",
                "sharpe": None,
                "sharpe_lo": None,
                "sharpe_hi": None,
                "sharpe_withheld": "too_few_periods:19/20@1",
                "ic_before": None,
                "days_before": None,
                "wiped_out_on": None,
                "words": {"sharpe": "not measured"},
            },
        ],
        "pagination": {"cursor": None, "has_more": False},
        "facts": {
            "rows_total": 2,
            "cells": {"computed": 1, "not_run": 1},
            "axes": [
                {"name": "fast", "label": "Fast", "role": "ordered"},
                {"name": "slow", "label": "Slow", "role": "ordered"},
            ],
            "measures": [
                {"name": "sharpe", "label": "Sharpe", "unit": "ratio"},
                {"name": "ic", "label": "Rank IC", "unit": "ratio"},
            ],
        },
    }


def test_m13_m14_a_data_table_reads_in_words_never_keys_or_tokens() -> None:
    """M13/M14 (S1): ``keel_research_get(view=…)`` and ``keel research data`` printed raw headers (sharpe_lo,
    wiped_out_on, b, status, cell) and cells ("not_run", "too_few_periods:19/20@1", "computed"). The one table
    formatter names every column in words, folds the interval into its measure, leaves bookkeeping out and words a
    row's state. Seed (reversed): ``hidden`` emptied in ``_table_lines`` reds the header and token arms."""
    text = rv.data_text(_table_page(), title="Every setting", version=1, call_args={})
    lines = text.splitlines()
    head = next(line for line in lines if line.startswith("| ") and "---" not in line)
    body = [line for line in lines if line.startswith("| ") and line != head and "---" not in line]
    assert len(body) == 2, text  # non-vacuity: both rows written
    assert head == "| Fast | Slow | State | Sharpe | Rank IC before the split | Days before the split |", head
    assert body[0] == "| 10 | 40 | measured | 1.17 (−0.21 to 2.41) | 0.03 | 120 |", body
    assert body[1].startswith("| 10 | 50 | not run | not measured |"), body
    assert "Rows 1–2 of 2 (1 measured, 1 not run)" in lines
    assert "No row was wiped out." in lines
    words = "\n".join(line for line in lines if not line.startswith("Keys for"))
    assert not SNAKE.search(words), SNAKE.findall(words)
    assert "too_few_periods" not in text and "computed" not in text and "not_run" not in text


def test_m15_m16_where_an_interval_is_missing_and_a_range_end_read_in_words() -> None:
    """M15: "Interval withheld at 2026-11-01T00:00:00: …"; M16: "Count 1 (Bin 0.9620258664953725)". Seed (reversed):
    ``_x_words`` returning ``_x_text(x)`` reds the first arm. Control: a horizon axis says its label and value."""
    facts = {
        "axes": [{"name": "time", "label": "Day", "kind": "time"}],
        "withheld": ["2026-11-01T00:00:00"],
        "withheld_reason": {"2026-11-01T00:00:00": "Withheld: 13 separate 1-day stretches."},
    }
    assert rv._view_fact_lines(facts) == ["No interval at Nov 1, 2026: 13 separate 1-day stretches."]
    ahead = {
        "axes": [{"name": "horizon", "label": "Days ahead", "role": "ordered"}],
        "withheld": [20],
        "withheld_reason": {"20": "13 stretches."},
    }
    assert rv._view_fact_lines(ahead) == ["No interval at days ahead 20: 13 stretches."]  # control
    end = {"at": {"bin_lo": 0.9620258664953725}}
    said = rv._end_at(end, {"axes": [{"name": "bin_lo", "label": "Bin"}], "bins": {"label": "Sharpe"}})
    assert said == " (Sharpe from 0.962)", said


def test_m9_m34_a_plan_counts_runs_and_the_cli_names_the_drafts_verb() -> None:
    """M9: "The backtest 1"; M34: the CLI's Draft step named two commands. Seed (reversed): the M34 ``re.sub`` in
    ``cli_text`` removed reds the CLI arm. Control: the MCP text keeps the plan-then-run call."""
    plan = {
        "study": {"title": "Momentum", "kind_label": {"name": "Check a strategy"}},
        "writes": "a new study, version 1",
        "words": {"work": "2 runs"},
        "steps": [{"id": "base", "label": "The backtest", "to_run": 1, "runnable": True}],
        "run_allowed": True,
        "plan_id": "pln_a",
    }
    assert "Steps (1): The backtest: 1 run" in rv.plan_text(plan)
    mcp = 'keel_research_plan(study_id="stu_a", version=1) plans its run; keel_research_run runs that plan.'
    assert cli_text(mcp) == "keel research run-draft stu_a --version 1 runs it."
    assert "keel_research_run runs that plan." in mcp  # control


def test_e20_e22_the_cli_reads_a_refusal_without_the_agents_parts() -> None:
    """E20: every "Plan refused" issue line on the CLI led with the document path ("/steps/map/axes/0 · …"); E22: a
    rate limit ended with the agent's "Rate limit, not a plan limit; retry in a few seconds.". Seed (reversed): the
    path ``re.sub`` in ``_cli_words`` removed reds the first arm. Control: the MCP text keeps the path for the
    agent's edit."""
    from keel.tools.outcomes._research_commands import cli_refusal_text, refusal_text

    env = {
        "code": "STUDY_ADDRESS_SYNTAX",
        "message": 'Plan refused: 1 issue; nothing was planned or spent.\n/steps/map/axes/0 · "x" is not an address.',
        "what_was_expected": "",
        "suggested_next_action": {"tool": None, "args": {}},
        "detail": {"links": {"app": "https://app.usekeel.io/research"}},
    }
    cli = cli_refusal_text(env).splitlines()
    assert cli[1] == '"x" is not an address.', cli
    assert '/steps/map/axes/0 · "x" is not an address.' in refusal_text(env).splitlines()  # control
    busy = {**env, "code": "rate_limited", "message": "This share is busy; try again later. Rate limit, not a plan limit; retry in a few seconds."}  # fmt: skip
    assert cli_refusal_text(busy).splitlines()[0] == "This share is busy; try again later."


def test_d407_a_shared_check_with_no_bars_says_the_copys_line() -> None:
    """D-407 (2): a share or Keel's showcase Check with no bars set is "Checks", never "Your checks", and ends with
    the copy's line ("Make it yours to set your own bars"), the words the app and the card say. Seed (reversed):
    the ``shared`` arm removed in ``_bars_lines`` reds this. Control: the owner's text keeps the owner's line."""
    env = _check_env(bars=False)
    env["study"]["share_id"] = "shr_abc"
    lines = rv.study_text(env).splitlines()
    assert "Checks (8; no bars set yet):" in lines, lines
    assert rv.NO_BARS_WORDS["copy"] == "Make it yours to set your own bars"
    assert "Make it yours to set your own bars" in lines, lines
    assert not any(line.startswith("Set a bar") for line in lines), lines
    assert len([line for line in lines if line.startswith("- Check ")]) == 8  # non-vacuity
    owner = rv.study_text(_check_env(bars=False))
    assert rv.NO_BARS_WORDS["owner"] in owner and "Make it yours" not in owner  # control
